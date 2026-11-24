import Link from "next/link";

export default function Home() {
  return (
    <main className="wrap narrow">
      <h1>Mocksie</h1>
      <p className="muted">Mock interview practice. Candidates open the link they were given. Interviewers sign in to the console.</p>
      <Link className="btn primary" href="/admin">
        Interviewer console
      </Link>
    </main>
  );
}
