export function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get: (name: string) =>
        name.toLowerCase() === "content-type" ? "application/json; charset=utf-8" : null,
    },
    json: async () => payload,
  } as unknown as Response;
}

export function nonJsonResponse(status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "text/html; charset=utf-8" },
    json: async () => "<html>error</html>",
  } as unknown as Response;
}

export function invalidJsonResponse(): Response {
  return {
    ok: true,
    status: 200,
    headers: { get: () => "application/json" },
    json: async () => {
      throw new SyntaxError("Unexpected token");
    },
  } as unknown as Response;
}
