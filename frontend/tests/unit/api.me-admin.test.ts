import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  api,
  type AdminUser,
  type AuditTrailEntry,
  type Invitation,
  type MeOut,
  type TotpSetupOut,
} from "@/lib/api";

const BASE = "/api/v1";

interface FetchCall {
  url: string;
  init: RequestInit | undefined;
}

let fetchMock: ReturnType<typeof vi.fn>;

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

function noContentResponse(): Response {
  return {
    ok: true,
    status: 204,
    json: async () => {
      throw new Error("204 has no body");
    },
  } as unknown as Response;
}

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function lastCall(): FetchCall {
  const call = fetchMock.mock.calls[0];
  if (!call) throw new Error("fetch was not called");
  return { url: String(call[0]), init: call[1] as RequestInit | undefined };
}

function meSample(overrides: Partial<MeOut> = {}): MeOut {
  return {
    id: 1,
    email: "user@example.com",
    display_name: "Test User",
    is_admin: false,
    is_impersonating: false,
    trading_mode: "manual",
    position_sizing_mode: "fixed",
    fixed_size_min_usdt: 20,
    fixed_size_max_usdt: 50,
    max_concurrent_positions: 5,
    max_leverage_cap: 3,
    quiet_hours_enabled: false,
    quiet_hours_start: null,
    quiet_hours_end: null,
    binance_keys_configured: false,
    telegram_configured: false,
    totp_configured: false,
    ...overrides,
  };
}

describe("api.me", () => {
  test("GET /me uses correct URL with credentials include", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(meSample()));
    const res = await api.me();
    const { url, init } = lastCall();
    expect(url).toBe(`${BASE}/me`);
    expect(init?.method).toBe("GET");
    expect(init?.credentials).toBe("include");
    expect(res.email).toBe("user@example.com");
  });
});

describe("api.mePatch", () => {
  test("PATCH /me with JSON body", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(meSample({ display_name: "New" })));
    const res = await api.mePatch({ display_name: "New" });
    const { url, init } = lastCall();
    expect(url).toBe(`${BASE}/me`);
    expect(init?.method).toBe("PATCH");
    expect(init?.headers).toMatchObject({ "Content-Type": "application/json" });
    expect(JSON.parse(String(init?.body))).toEqual({ display_name: "New" });
    expect(res.display_name).toBe("New");
  });
});

describe("api.meBinanceKeys", () => {
  test("POST /me/binance-keys with both fields, returns void on 204", async () => {
    fetchMock.mockResolvedValueOnce(noContentResponse());
    const res = await api.meBinanceKeys("KEY", "SECRET");
    const { url, init } = lastCall();
    expect(url).toBe(`${BASE}/me/binance-keys`);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ api_key: "KEY", api_secret: "SECRET" });
    expect(res).toBeUndefined();
  });
});

describe("api.meTelegram", () => {
  test("POST /me/telegram with bot_token + chat_id", async () => {
    fetchMock.mockResolvedValueOnce(noContentResponse());
    await api.meTelegram("bot:abc", "12345");
    const { url, init } = lastCall();
    expect(url).toBe(`${BASE}/me/telegram`);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ bot_token: "bot:abc", chat_id: "12345" });
  });
});

describe("api.meTotpSetup", () => {
  test("POST /me/totp/setup returns provisioning_uri + backup_codes", async () => {
    const sample: TotpSetupOut = {
      provisioning_uri: "otpauth://totp/Trading%20Radar:user@example.com?secret=ABC",
      secret_for_display: "ABC",
      backup_codes: ["a1", "b2", "c3"],
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(sample));
    const res = await api.meTotpSetup();
    const { url, init } = lastCall();
    expect(url).toBe(`${BASE}/me/totp/setup`);
    expect(init?.method).toBe("POST");
    expect(res.provisioning_uri).toContain("otpauth://");
    expect(res.backup_codes).toHaveLength(3);
  });
});

describe("api.meTotpVerify", () => {
  test("POST /me/totp/verify with code", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));
    const res = await api.meTotpVerify("123456");
    const { url, init } = lastCall();
    expect(url).toBe(`${BASE}/me/totp/verify`);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ code: "123456" });
    expect(res.ok).toBe(true);
  });
});

describe("api.adminListUsers", () => {
  test("GET /admin/users returns AdminUser[]", async () => {
    const sample: AdminUser[] = [
      {
        id: 1,
        email: "admin@example.com",
        display_name: "Admin",
        is_admin: true,
        is_active: true,
        trading_mode: "manual",
        last_login: null,
        created_at: "2026-05-01T00:00:00Z",
        invited_by: null,
      },
    ];
    fetchMock.mockResolvedValueOnce(jsonResponse(sample));
    const res = await api.adminListUsers();
    expect(lastCall().url).toBe(`${BASE}/admin/users`);
    expect(res).toHaveLength(1);
    expect(res[0]?.is_admin).toBe(true);
  });
});

describe("api.adminCreateInvitation", () => {
  test("POST /admin/invitations with body", async () => {
    const sample: Invitation = {
      id: 7,
      email: "new@example.com",
      display_name: "New",
      invited_by: 1,
      invited_at: "2026-05-04T00:00:00Z",
      accepted_at: null,
      cf_access_added: false,
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(sample, 201));
    const res = await api.adminCreateInvitation({
      email: "new@example.com",
      display_name: "New",
      is_admin: false,
    });
    const { url, init } = lastCall();
    expect(url).toBe(`${BASE}/admin/invitations`);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      email: "new@example.com",
      display_name: "New",
      is_admin: false,
    });
    expect(res.id).toBe(7);
  });
});

describe("api.adminPatchUser", () => {
  test("PATCH /admin/users/{id} with patch body", async () => {
    const sample: AdminUser = {
      id: 5,
      email: "u@e.com",
      display_name: "U",
      is_admin: true,
      is_active: false,
      trading_mode: "manual",
      last_login: null,
      created_at: "2026-05-01T00:00:00Z",
      invited_by: 1,
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(sample));
    await api.adminPatchUser(5, { is_active: false, is_admin: true });
    const { url, init } = lastCall();
    expect(url).toBe(`${BASE}/admin/users/5`);
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ is_active: false, is_admin: true });
  });
});

describe("api.adminDeleteUser", () => {
  test("DELETE /admin/users/{id} → void on 204", async () => {
    fetchMock.mockResolvedValueOnce(noContentResponse());
    const res = await api.adminDeleteUser(99);
    const { url, init } = lastCall();
    expect(url).toBe(`${BASE}/admin/users/99`);
    expect(init?.method).toBe("DELETE");
    expect(res).toBeUndefined();
  });
});

describe("api.adminImpersonate", () => {
  test("POST /admin/impersonate/{id}", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      admin_user_id: 1,
      target_user_id: 7,
      started_at: "2026-05-04T00:00:00Z",
    }));
    const res = await api.adminImpersonate(7);
    const { url, init } = lastCall();
    expect(url).toBe(`${BASE}/admin/impersonate/7`);
    expect(init?.method).toBe("POST");
    expect(res.target_user_id).toBe(7);
  });
});

describe("api.adminImpersonateClear", () => {
  test("DELETE /admin/impersonate", async () => {
    fetchMock.mockResolvedValueOnce(noContentResponse());
    const res = await api.adminImpersonateClear();
    const { url, init } = lastCall();
    expect(url).toBe(`${BASE}/admin/impersonate`);
    expect(init?.method).toBe("DELETE");
    expect(res).toBeUndefined();
  });
});

describe("api.adminAuditTrail", () => {
  test("no filters → bare URL", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await api.adminAuditTrail();
    expect(lastCall().url).toBe(`${BASE}/admin/audit-trail`);
  });

  test("encodes user_id, table, since, limit, offset as query params", async () => {
    const sample: AuditTrailEntry[] = [
      {
        table_name: "predictions",
        row_id: 1,
        user_id: 1,
        ts: "2026-05-01T00:00:00Z",
        summary: "BTC LONG",
      },
    ];
    fetchMock.mockResolvedValueOnce(jsonResponse(sample));
    await api.adminAuditTrail({
      user_id: 7,
      table: "predictions",
      since: "2026-05-01T00:00:00Z",
      limit: 50,
      offset: 100,
    });
    const url = lastCall().url;
    expect(url.startsWith(`${BASE}/admin/audit-trail?`)).toBe(true);
    expect(url).toContain("user_id=7");
    expect(url).toContain("table=predictions");
    expect(url).toContain("since=2026-05-01T00%3A00%3A00Z");
    expect(url).toContain("limit=50");
    expect(url).toContain("offset=100");
  });
});
