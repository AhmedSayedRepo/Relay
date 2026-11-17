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
  // Inside the Relay Operator app the WebView cannot download: hand it over.
  const bridge = (window as { RelayAndroid?: { saveFile(name: string, mime: string, data: string): boolean } }).RelayAndroid;
  if (bridge) {
    const bytes = new Uint8Array(await blob.arrayBuffer()); let binary = "";
    for (let index = 0; index < bytes.length; index += 0x8000) binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
    if (!bridge.saveFile(filename, blob.type || "application/octet-stream", btoa(binary))) throw new ApiError(500, "Export could not be saved.");
    return;
  }
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

/** JSON request that may run without a token (sign-in recovery) and may return 204. */
export async function send<T = null>(
  path: string,
  body: unknown,
  { token, method = "POST" }: { token?: string; method?: string } = {},
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new ApiError(response.status, `Request failed (${response.status}).`);
  if (response.status === 204) return null as T;
  return (await response.json()) as T;
}
