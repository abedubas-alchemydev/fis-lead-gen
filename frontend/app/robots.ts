import type { MetadataRoute } from "next";

// Keep tokenized public-share URLs out of crawlers/search results. The
// share pages also carry per-page `robots: { index: false }` metadata —
// this file is the site-wide belt to that suspenders.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: "/share/",
      },
    ],
  };
}
