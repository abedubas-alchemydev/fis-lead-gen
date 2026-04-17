const METADATA_SERVER_URL =
  "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity";
const REFRESH_WINDOW_SECONDS = 300;

type CachedToken = {
  token: string;
  expiresAt: number;
};

const tokenCache = new Map<string, CachedToken>();

export async function getIdentityToken(audience: string): Promise<string | null> {
  if (!process.env.K_SERVICE) {
    return null;
  }

  const nowSeconds = Math.floor(Date.now() / 1000);
  const cached = tokenCache.get(audience);
  if (cached && cached.expiresAt - nowSeconds > REFRESH_WINDOW_SECONDS) {
    return cached.token;
  }

  try {
    const response = await fetch(
      `${METADATA_SERVER_URL}?audience=${encodeURIComponent(audience)}`,
      { headers: { "Metadata-Flavor": "Google" }, cache: "no-store" }
    );
    if (!response.ok) {
      console.error(
        `[gcp-identity-token] metadata server returned ${response.status} for audience=${audience}`
      );
      return null;
    }

    const token = (await response.text()).trim();
    const expiresAt = decodeJwtExpiry(token);
    if (expiresAt === null) {
      console.error(
        `[gcp-identity-token] could not decode exp claim from token for audience=${audience}`
      );
      return null;
    }

    tokenCache.set(audience, { token, expiresAt });
    return token;
  } catch (error) {
    console.error(
      `[gcp-identity-token] failed to fetch token for audience=${audience}`,
      error
    );
    return null;
  }
}

function decodeJwtExpiry(token: string): number | null {
  try {
    const parts = token.split(".");
    if (parts.length < 2) {
      return null;
    }
    const payload = JSON.parse(
      Buffer.from(parts[1], "base64url").toString("utf8")
    ) as { exp?: unknown };
    return typeof payload.exp === "number" ? payload.exp : null;
  } catch {
    return null;
  }
}
