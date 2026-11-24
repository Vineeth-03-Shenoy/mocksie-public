"""The scrubber is rule-based: interviewer-typed names/terms + patterns. Applied to resumes AND live transcripts."""
import json
import re

import pytest

from backend.config import Settings
from backend.guardrails.pii import Redactor, find_pii, scrub
from backend.guardrails.resume_sections import drop_sections, redact_resume
from backend.llm.client import LLMClient, PIIBlocked, PIIGuard
from backend.llm.usage import UsageTracker


# ---------------------------------------------------------------- candidate name
def test_a_candidate_name_is_required():
    for bad in ("", "   ", "N.", "A B"):
        with pytest.raises(ValueError):
            Redactor(bad)


@pytest.mark.parametrize("name,text,expected", [
    ("Priya Rhea Testname", "I am Priya Rhea Testname, a final year student.", "I am [NAME], a final year student."),
    ("Priya Rhea Testname", "Call me Rhea. Testname built it. PRIYA agrees.", "Call me [NAME]. [NAME] built it. [NAME] agrees."),
    ("Priya Rhea Testname", "priya   rhea\ntestname wrote the code", "[NAME] wrote the code"),
    ("Mohammed Irfan", "Mohammed Irfan and mohammed irfan and Irfan's project", "[NAME] and [NAME] and [NAME]'s project"),
    ("Mohammed Irfan", "M. Irfan fixed it; Irfan M. reviewed it", "[NAME] fixed it; [NAME] reviewed it"),
    ("Lakshmi N.", "Lakshmi N. built a parser", "[NAME] built a parser"),
    ("Lakshmi N.", "N. Lakshmi built a parser; Lakshmi built it", "[NAME] built a parser; [NAME] built it"),
    ("Lakshmi Narayanan", "L. Narayanan wrote tests; S. R. Lakshmi did not", "[NAME] wrote tests; S. R. [NAME] did not"),
    ("Anne-Marie D'Souza", "Anne-Marie, Marie and D'Souza", "[NAME], [NAME] and [NAME]"),
])
def test_candidate_name_full_and_each_part_whole_word_case_insensitive(name, text, expected):
    assert Redactor(name).redact(text) == expected


def test_whole_word_only_and_ordinary_words_survive():
    r = Redactor("Priya Rhea Testname")
    assert r.redact("He said it was easy; Saint Louis; Testnames") == "He said it was easy; Saint Louis; Testnames"
    assert r.redact("A stack is LIFO. I used A and B.") == "A stack is LIFO. I used A and B."


def test_an_initial_is_only_removed_next_to_a_name_and_only_if_it_belongs_to_the_name():
    r = Redactor("Lakshmi N.")
    assert r.redact("Lakshmi A good student; plan B; N is the size") == "[NAME] A good student; plan B; N is the size"


# ---------------------------------------------------------------- "Also hide"
def test_also_hide_terms_are_matched_like_names():
    r = Redactor("Asha Verma", "Northfield College of Engineering, Hyderabad, Dr Ravi Kumar")
    out = r.redact("At Northfield College of Engineering in Hyderabad my mentor Ravi Kumar (Dr Kumar) helped Asha.")
    assert "Northfield" not in out and "Hyderabad" not in out and "Ravi" not in out and "Kumar" not in out
    assert out.count("[REDACTED]") >= 3 and "[NAME]" in out
    # generic words from a hidden phrase are not redacted on their own
    assert "engineering" in r.redact("I studied engineering and technology")
    assert r.redact("The College of Engineering is large") == "The College of Engineering is large"


def test_also_hide_accepts_commas_semicolons_and_blanks_and_ignores_single_letters():
    r = Redactor("Asha Verma", " Lakeview Tech ;; Pune , x, ")
    assert r.redact("Lakeview Tech, Pune and x marks") == "[REDACTED], [REDACTED] and x marks"


def test_literal_terms_without_plain_words_are_matched():
    r = Redactor("Asha Verma", "9th Cross")
    assert r.redact("lives on 9th Cross road") == "lives on [REDACTED] road"


# ---------------------------------------------------------------- patterns
@pytest.mark.parametrize("text,token", [
    ("mail asha.verma@example.com now", "[EMAIL]"),
    ("call +91 98765 43210", "[PHONE]"), ("call +91-9876543210", "[PHONE]"), ("call 9876543210", "[PHONE]"),
    ("call 098765 43210", "[PHONE]"), ("call (+91) 98765-43210", "[PHONE]"), ("call 98765 43210", "[PHONE]"),
    ("call 080-23456789", "[PHONE]"), ("call 987-654-3210", "[PHONE]"),
    ("see linkedin.com/in/asha-verma", "[LINK]"), ("see https://github.com/ashav/inventory", "[LINK]"),
    ("see www.asha.dev/portfolio", "[LINK]"), ("see leetcode.com/u/asha", "[LINK]"), ("see asha.github.io/me", "[LINK]"),
    ("LinkedIn: asha-verma", "[LINK]"), ("GitHub - ashav", "[LINK]"), ("follow @asha_v", "[LINK]"),
    ("aadhaar 1234 5678 9012", "[ID]"), ("aadhaar 123456789012", "[ID]"), ("PAN ABCDE1234F", "[ID]"),
    ("Roll No: 1AB21CS045", "[ID]"), ("usn 1AB21CS045", "[ID]"),
    ("born 12/03/2004", "[REDACTED]"), ("born 12-03-2004", "[REDACTED]"), ("born 12.03.2004", "[REDACTED]"),
    ("born 12 March 2004", "[REDACTED]"), ("born 12th Mar, 2004", "[REDACTED]"), ("born March 12, 2004", "[REDACTED]"),
    ("PIN: 560001", "[REDACTED]"), ("Pincode 500 081", "[REDACTED]"), ("pin code - 500081", "[REDACTED]"),
])
def test_each_pattern_is_replaced_with_its_typed_token(text, token):
    r = Redactor("Asha Verma")
    out = r.redact(text)
    assert token in out, (text, out)
    assert not re.search(r"\d{4}|asha-verma|ashav|asha_v|\.com/", out), out
    assert find_pii(out) == [], out  # nothing left for the tripwire to catch


def test_pin_codes_in_address_lines_are_redacted_on_resumes_only():
    r = Redactor("Asha Verma")
    assert r.redact("Bengaluru - 560001", resume=True) == "Bengaluru - [REDACTED]"
    assert r.redact("Hyderabad, 500 081", resume=True) == "Hyderabad, [REDACTED]"
    assert r.redact("it handled n, 100000 users") == "it handled n, 100000 users"  # speech / code is left alone


@pytest.mark.parametrize("text", [
    "Use @Override in Java.", "A roll number suggests a hash map.", "Rollback undoes a transaction.",
    "Complexity is O(n log n) for 1,000,000 items.", "fib(90) is 2880067194370816120.", "Python 3.8.18 on Linux",
    "java.io and java.net are packages", "It took 12 March of effort", "I scored 98.5 percent", "port 8080",
    "Name the pins on the board", "Version 2.4.6 in 2024",
])
def test_ordinary_interview_content_is_untouched(text):
    assert Redactor("Asha Verma").redact(text) == text


def test_scrub_is_patterns_only_and_idempotent():
    once = scrub("mail a@b.co or call 9876543210")
    assert once == "mail [EMAIL] or call [PHONE]" and scrub(once) == once


def test_pattern_wins_over_name_inside_an_email_or_link():
    out = Redactor("Asha Verma").redact("asha.verma@example.com / linkedin.com/in/asha-verma / Asha")
    assert out == "[EMAIL] / [LINK] / [NAME]"


def test_replacement_tokens_keep_the_answer_readable():
    r = Redactor("Priya Rhea Testname", "Hyderabad")
    out = r.redact("Hi, Testname here from Hyderabad (9876543210). I built a cache; mail me at rhea@mailbox.test.")
    assert out == "Hi, [NAME] here from [REDACTED] ([PHONE]). I built a cache; mail me at [EMAIL]."


# ---------------------------------------------------------------- dropped resume sections
RESUME = """Priya Rhea Testname
EDUCATION
B.Tech CSE 2026, CGPA 8.4
Projects
Address validation service built with Flask and unit tests
Skills
Python, SQL
{drop}
Awards
Winner, college hackathon
"""


@pytest.mark.parametrize("drop,gone,heading", [
    ("References\nProf. R. Rao, Principal\nPhone available on request", ("Rao", "Principal"), "References"),
    ("Referees:\nDr Meena, HoD", ("Meena",), "Referees"),
    ("Address: 12, MG Road,\nBengaluru - 560001", ("MG Road", "560001"), "Address"),
    ("ADDRESS\n12 MG Road\nBengaluru", ("MG Road",), "ADDRESS"),
    ("Permanent Address: Flat 4, Green Park", ("Green Park",), "Permanent Address"),
    ("DECLARATION\nI hereby declare that the above is true.\nPlace: Hyderabad", ("hereby declare", "Place"), "DECLARATION"),
    ("Declaration: I hereby declare all of this is true", ("hereby declare",), "Declaration"),
    ("Personal Details\nGender: Female\nNationality: Indian\nMarital Status: Single", ("Gender", "Nationality", "Single"), "Personal Details"),
    ("Personal Information\nLanguages known: Telugu", ("Telugu",), "Personal Information"),
    ("Father's Name: Mr K. Rao", ("K. Rao",), "Father's Name"),
    ("Mother's Name: Mrs L. Rao", ("L. Rao",), "Mother's Name"),
    ("Date of Birth: 12 March 2004", ("2004",), "Date of Birth"),
    ("DOB: 12/03/2004", ("12/03",), "DOB"),
])
def test_each_sensitive_section_is_dropped_and_the_rest_survives(drop, gone, heading):
    text = RESUME.format(drop=drop)
    out, dropped = drop_sections(text)
    assert heading in dropped
    for g in gone:
        assert g not in out, (g, out)
    for kept in ("B.Tech CSE 2026", "Address validation service built with Flask", "Python, SQL", "Winner, college hackathon", "Awards"):
        assert kept in out, (kept, out)


def test_a_section_at_the_end_of_the_resume_is_dropped_to_the_end():
    out, dropped = drop_sections("Skills\nPython\nDeclaration\nI hereby declare\nPriya Rhea Testname")
    assert out == "Skills\nPython" and dropped == ["Declaration"]


def test_sentences_that_merely_start_with_a_section_word_are_kept():
    text = "Address book app in Java\nDeclaration of variables is covered\nReferences to pointers"
    assert drop_sections(text) == (text, [])


def test_redact_resume_drops_sections_then_redacts_names_and_patterns():
    r = Redactor("Priya Rhea Testname", "Northfield College")
    raw = ("Priya Rhea Testname | rhea.t@mailbox.test | +91 98765 43210 | github.com/rheat\n"
           "Education\nNorthfield College, B.Tech CSE\nProjects\nBuilt an LRU cache; Testname owned the design.\n"
           "Personal Details\nDOB: 12/03/2004\nAddress: 5 Park Road, Pune - 411001\nDeclaration\nTrue.\nSai")
    out, dropped = redact_resume(raw, r)
    assert out.splitlines()[0] == "[NAME] | [EMAIL] | [PHONE] | [LINK]"
    assert "[REDACTED], B.Tech CSE" in out and "[NAME] owned the design" in out
    assert "2004" not in out and "Park Road" not in out and "411001" not in out and "True." not in out
    assert {"Personal Details", "DOB", "Address", "Declaration"} <= set(dropped)


# ---------------------------------------------------------------- nothing typed reaches OpenAI
def make_client(tmp_path):
    s = Settings(openai_api_key="x", interviewer_model="m-int", fast_model="m-fast", judge_model="m-judge", store=False,
                 pool_small=10**6, pool_judge=10**6, judge_over_budget="queue", judge_queue_threshold=0.9,
                 database_path=str(tmp_path / "u.db"))
    sent = []

    def transport(payload):
        sent.append(json.dumps(payload, ensure_ascii=False))
        return {"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    return LLMClient(s, UsageTracker(s.database_path, {"small": s.pool_small, "judge": s.pool_judge}), PIIGuard(), transport), sent


NAME = "Priya Rhea Testname"
HIDE = "Northfield College, Hyderabad, Dr Ravi Kumar"
TYPED = ["Priya", "Rhea", "Testname", "Northfield College", "Northfield", "Hyderabad", "Ravi", "Kumar"]
TRANSCRIPT = [
    "Hello, I am Priya Rhea Testname from Northfield College in Hyderabad.",
    "My mentor Dr Ravi Kumar taught me. Call me rhea, or TESTNAME.",
    "I built a cache and my email is rhea.t@mailbox.test, phone 98765 43210.",
]


def whole_word(term, text):
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.I) is not None


def test_no_typed_name_or_term_reaches_the_openai_client_after_redaction(tmp_path):
    client, sent = make_client(tmp_path)
    r = Redactor(NAME, HIDE)
    client.guard.attach(r)
    for line in TRANSCRIPT:
        client.complete("interviewer", [{"role": "user", "content": r.redact(line)}])
    resume, _ = redact_resume("Priya Rhea Testname\nSkills\nPython\nReferences\nDr Ravi Kumar\n", r)
    client.complete("fast", [{"role": "system", "content": "extract"}, {"role": "user", "content": resume}])
    assert len(sent) == len(TRANSCRIPT) + 1
    for payload in sent:
        for term in TYPED:
            assert not whole_word(term, payload), (term, payload)
        assert "mailbox" not in payload and "98765" not in payload


@pytest.mark.parametrize("leak", [
    "Hello Priya", "ask rhea", "TESTNAME wrote this", "Northfield College", "a college in hyderabad", "thanks, Dr Kumar", "Ravi's idea",
])
def test_unredacted_typed_terms_are_blocked_before_any_network_call(tmp_path, leak):
    client, sent = make_client(tmp_path)
    client.guard.attach(Redactor(NAME, HIDE))
    with pytest.raises(PIIBlocked):
        client.complete("interviewer", [{"role": "user", "content": leak}])
    with pytest.raises(PIIBlocked):  # also when the text sits in a later message of the conversation
        client.complete("interviewer", [{"role": "system", "content": "be brief"}, {"role": "assistant", "content": "ok"},
                                        {"role": "user", "content": f"about {leak}"}])
    assert sent == []


def test_our_own_static_prompts_may_contain_words_that_are_also_name_parts(tmp_path):
    client, sent = make_client(tmp_path)
    client.guard.attach(Redactor("Test Candidate Mark", "Grace"))
    schema = {"type": "object", "properties": {"mark": {"type": "string", "description": "the candidate mark"}}}
    client.complete("fast", [{"role": "system", "content": "Rate the candidate. Mark each answer."},
                             {"role": "user", "content": "[NAME] answered well"}],
                    tools=[{"type": "function", "function": {"name": "t", "description": "Mark the candidate done"}}],
                    response_format={"type": "json_schema", "json_schema": {"name": "x", "strict": True, "schema": schema}})
    assert len(sent) == 1
    client.complete("fast", [{"role": "user", "content": "[NAME] answered"}, {"role": "assistant", "content": "Thanks. Tell me about the test you wrote, Mark."},
                             {"role": "user", "content": "[NAME] wrote it"}])           # the model's own earlier reply may use ordinary words
    assert len(sent) == 2
    with pytest.raises(PIIBlocked):  # but the same word in candidate-supplied (user) text is blocked
        client.complete("fast", [{"role": "user", "content": "the candidate said hi"}])
    with pytest.raises(PIIBlocked):  # and contact details are caught everywhere, even in static text
        client.complete("fast", [{"role": "system", "content": "mail a@b.co"}])


def test_tripwire_does_not_block_normal_words_and_is_only_armed_by_the_interviewers_terms(tmp_path):
    client, sent = make_client(tmp_path)
    client.guard.attach(Redactor(NAME, HIDE))
    client.complete("interviewer", [{"role": "user", "content": "He said a saint explained hash maps."}])
    assert len(sent) == 1


def test_names_the_interviewer_did_not_type_are_not_detected_accepted_pilot_risk():
    # Documented in SECURITY.md: friends, mentors and managers are not recognised by a rule-based scrubber.
    r = Redactor(NAME)
    assert r.redact("My friend Karthik helped") == "My friend Karthik helped"
