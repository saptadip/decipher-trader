function url(path: string): string {
  const base = process.env.CONTROL_PLANE_URL;
  if (!base) throw new Error("CONTROL_PLANE_URL not set");
  return `${base.replace(/\/$/, "")}${path.startsWith("/") ? path : `/${path}`}`;
}

function headers(): Record<string, string> {
  const token = process.env.OPERATOR_TOKEN;
  if (!token) throw new Error("OPERATOR_TOKEN not set");
  return { authorization: `Bearer ${token}`, "content-type": "application/json" };
}

export const controlPlane = {
  get: (path: string) => fetch(url(path), { headers: headers() }),
  post: (path: string, body: unknown) =>
    fetch(url(path), {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(body ?? {}),
    }),
  delete: (path: string) => fetch(url(path), { method: "DELETE", headers: headers() }),
};
