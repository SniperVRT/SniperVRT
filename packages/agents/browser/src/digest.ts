import type { Page } from "playwright";
import type { PageDigest } from "./types.js";

// The ENTIRE reason we use playwright + a digest instead of dumping DOM:
// tokens. Model-facing views should be actionable-only, with short text.
export async function buildPageDigest(page: Page, maxTextChars = 2000): Promise<PageDigest> {
  const url = page.url();
  const title = await page.title();
  const actionables = await extractActionables(page);
  const textDigest = await extractTextDigest(page, maxTextChars);
  return { url, title, actionables, textDigest };
}

async function extractActionables(page: Page): Promise<string> {
  const rows = await page.evaluate(() => {
    function describe(el: Element): string | null {
      const tag = el.tagName.toLowerCase();
      const id = (el as HTMLElement).id ? `#${(el as HTMLElement).id}` : "";
      const name = el.getAttribute("name");
      const role = el.getAttribute("role");
      const ariaLabel = el.getAttribute("aria-label");
      const placeholder = el.getAttribute("placeholder");
      const text = (el.textContent ?? "").trim().slice(0, 60);
      const href = (el as HTMLAnchorElement).href;
      const type = (el as HTMLInputElement).type;
      const selectorHints = [tag, id, name ? `[name=${name}]` : null]
        .filter(Boolean)
        .join("");
      const label = ariaLabel ?? placeholder ?? text ?? role ?? "";
      if (!selectorHints && !label && !href) return null;
      return `${selectorHints || tag}${type ? `[type=${type}]` : ""} :: ${label}${href ? ` -> ${href}` : ""}`;
    }
    const selectors = [
      "a[href]",
      "button",
      "input",
      "select",
      "textarea",
      "[role=button]",
      "[role=link]",
      "[role=textbox]",
      "[onclick]",
    ];
    const out: string[] = [];
    for (const s of selectors) {
      const nodes = Array.from(document.querySelectorAll(s)).slice(0, 30);
      for (const n of nodes) {
        const d = describe(n);
        if (d) out.push(d);
      }
    }
    return out.slice(0, 80);
  });
  return rows.join("\n");
}

async function extractTextDigest(page: Page, max: number): Promise<string> {
  const text = await page.evaluate(() => {
    const body = document.body?.innerText ?? "";
    return body.replace(/\s+/g, " ").slice(0, 8000);
  });
  return text.length > max ? `${text.slice(0, max)}… (${text.length - max} chars omitted)` : text;
}
