export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  token: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try {
      const body = (await response.json()) as { error?: { message?: string } };
      message = body.error?.message || message;
    } catch {
      // The stable fallback avoids exposing an untrusted response body.
    }
    throw new ApiError(response.status, message);
  }
  return (await response.json()) as T;
}

export function query(values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

export async function downloadExport(path: string, token: string): Promise<void> {
  const response = await fetch(`/api/v1${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new ApiError(response.status, "Export could not be created.");
  const blob = await response.blob();
  const content = response.headers.get("Content-Disposition") || "";
  const filename = content.match(/filename="([^"]+)"/)?.[1] || "relay-export.xlsx";
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
