"use client";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const router = useRouter();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const resp = await fetch("/api/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (resp.ok) router.push("/");
    else setErr("Invalid credentials.");
  }

  return (
    <main style={{ maxWidth: 320, margin: "80px auto", padding: 24 }}>
      <h1>Sign in</h1>
      <form onSubmit={onSubmit}>
        <label>Username<br /><input value={username} onChange={(e) => setUsername(e.target.value)} /></label>
        <br /><br />
        <label>Password<br /><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <br /><br />
        <button type="submit">Sign in</button>
        {err ? <p style={{ color: "red" }}>{err}</p> : null}
      </form>
    </main>
  );
}
