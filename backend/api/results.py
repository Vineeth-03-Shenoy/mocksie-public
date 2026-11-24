"""Results routes: interviewer login only. The candidate token can never reach these (they sit on the admin router)."""
from __future__ import annotations

import time
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from backend.scoring import form as formmod
from backend.scoring import results as res
from backend.scoring.service import ScoringService
from backend.store import Store

STATUS_LABEL = {"created": "Not started", "active": "In progress", "finished": "Scoring", "scoring": "Scoring", "ready": "Ready",
                "finalized": "Finalized"}


class Override(BaseModel):
    kind: str = Field(pattern="^(criterion|text|assessment|recommendation)$")
    key: str = Field(default="", max_length=120)
    value: int | str | None = None        # None clears the override
    note: str = Field(default="", max_length=500)


def build_results_router(*, store: Store, service: ScoringService, require_admin: Callable, clock: Callable[[], float]) -> APIRouter:
    r = APIRouter(prefix="/api/admin/interviews", dependencies=[Depends(require_admin)])

    def get(interview_id: str) -> dict:
        rec = store.get(interview_id)
        if not rec:
            raise HTTPException(404, "unknown interview")
        return rec

    def view_for(rec: dict) -> dict:
        d = rec["data"]
        sc = d.get("scoring") or {}
        out = {"id": rec["id"], "candidate_name": d["candidate_name"], "status": rec["status"], "status_label": STATUS_LABEL.get(rec["status"], rec["status"]),
               "scoring": {"state": sc.get("state"), "message": sc.get("message", "")}, "finalized_at": d.get("finalized_at")}
        if sc.get("state") != "ready":
            return out
        result, form = sc["result"], sc["form"]
        view = d.get("final_view") if rec["status"] == "finalized" and d.get("final_view") else res.build_view(form, result, d.get("overrides") or {})
        out.update(view=view, options={"assessment": formmod.assessment_options(form), "recommendation": formmod.recommendation_options(form)},
                   questions=result["questions"], coding=result["coding"], integrity=result["integrity"],
                   warnings=result.get("warnings", []), prep_references=result.get("prep_references", []),
                   transcript=[{"t": t["t"], "role": t["role"], "text": t["text"]} for t in (d["engine"]["transcript"]) if t["role"] in ("interviewer", "candidate")],
                   profile=d.get("profile"), profile_warnings=d.get("profile_warnings", []))
        return out

    @r.get("/{interview_id}/results")
    def results(interview_id: str):
        return view_for(get(interview_id))

    @r.post("/{interview_id}/score")
    def score(interview_id: str):
        rec = get(interview_id)
        if rec["status"] in ("created", "active"):
            raise HTTPException(409, "the interview is not finished yet")
        if rec["status"] == "finalized":
            raise HTTPException(409, "this result is finalized")
        state = service.score(interview_id)
        return {"state": state, **view_for(get(interview_id))["scoring"]}

    @r.put("/{interview_id}/overrides")
    def override(interview_id: str, body: Override):
        with store.lock(interview_id):
            rec = get(interview_id)
            if rec["status"] == "finalized":
                raise HTTPException(409, "this result is finalized and locked")
            sc = (rec["data"].get("scoring") or {})
            form = sc.get("form") or formmod.load_default()
            ov = rec["data"].setdefault("overrides", {"criteria": {}, "text": {}})
            if body.kind == "criterion":
                crit = next((c for c in form["criteria"] if c["id"] == body.key), None)
                if not crit:
                    raise HTTPException(422, "unknown criterion")
                if crit["id"] == "overall":
                    raise HTTPException(422, "the overall score is calculated from the other criteria; change those instead")
                if body.value is None:
                    ov["criteria"].pop(body.key, None)
                else:
                    lo, hi = crit["scale"]
                    if not isinstance(body.value, int) or not lo <= body.value <= hi:
                        raise HTTPException(422, f"value must be a whole number from {lo} to {hi}")
                    ov["criteria"][body.key] = {"value": body.value, "note": body.note}
            elif body.kind == "text":
                if body.key not in form.get("text_fields", []):
                    raise HTTPException(422, "unknown text field")
                if body.value is None:
                    ov["text"].pop(body.key, None)
                elif isinstance(body.value, str) and len(body.value) <= 2000:
                    ov["text"][body.key] = body.value
                else:
                    raise HTTPException(422, "text must be at most 2000 characters")
            else:
                options = formmod.assessment_options(form) if body.kind == "assessment" else formmod.recommendation_options(form)
                if body.value is None:
                    ov.pop(body.kind, None)
                elif body.value in options:
                    ov[body.kind] = body.value
                else:
                    raise HTTPException(422, "pick one of: " + "; ".join(options))
            store.save(rec)
        return view_for(get(interview_id))

    @r.post("/{interview_id}/finalize")
    def finalize(interview_id: str):
        with store.lock(interview_id):
            rec = get(interview_id)
            if rec["status"] == "finalized":
                raise HTTPException(409, "already finalized")
            if rec["status"] != "ready":
                raise HTTPException(409, "results are not ready to finalize")
            rec["status"], rec["data"]["finalized_at"] = "finalized", clock()
            rec["data"]["final_view"] = view_for(rec)["view"]  # frozen copy: later template edits cannot change it
            store.save(rec)
        return view_for(get(interview_id))

    @r.get("/{interview_id}/results.csv")
    def csv_export(interview_id: str):
        rec = get(interview_id)
        v = view_for(rec)
        if "view" not in v:
            raise HTTPException(409, "results are not ready")
        name = f"mocksie-{rec['id']}.csv"
        return Response(res.to_csv(v["view"]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    return r
