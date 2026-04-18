import { request } from "undici";
import { OrchestratorError } from "@snipervrt/shared";

// Minimal Google OAuth2 refresh-token exchange. No user-facing flow here -
// the deployer provides a long-lived refresh token via env; we exchange it
// for access tokens on demand, cached until near expiry.

export interface GoogleAuthConfig {
  clientId: string;
  clientSecret: string;
  refreshToken: string;
}

export class GoogleAuth {
  private cached?: { token: string; exp: number };
  constructor(private readonly cfg: GoogleAuthConfig) {}

  async accessToken(): Promise<string> {
    const now = Date.now();
    if (this.cached && this.cached.exp - 60_000 > now) return this.cached.token;

    const body = new URLSearchParams({
      client_id: this.cfg.clientId,
      client_secret: this.cfg.clientSecret,
      refresh_token: this.cfg.refreshToken,
      grant_type: "refresh_token",
    });

    const res = await request("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });
    if (res.statusCode >= 400) {
      throw new OrchestratorError("CONNECTOR_AUTH_FAILED", `google oauth ${res.statusCode}`);
    }
    const json = (await res.body.json()) as { access_token: string; expires_in: number };
    this.cached = {
      token: json.access_token,
      exp: Date.now() + json.expires_in * 1000,
    };
    return json.access_token;
  }
}
