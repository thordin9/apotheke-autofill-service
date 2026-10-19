import type { ClientContext } from "./context";

/** Feature 9 v3_lean list, used when the client sends no categories.
 * The prompt then matches bench/results/brandserial-2026-10-19/prompts/v3_lean.txt
 * without that file's trailing newline. There is no app/backends/cloudflare.py
 * on main; this is the prompt and schema the Python service sends today.
 */
export const V3_CATEGORIES = [
  "General",
  "Tools",
  "Electronics",
  "Kitchen",
  "Documents",
  "Hobby",
  "Clothing",
  "Shoes",
  "Books",
  "Lighting",
];

export function categoriesFor(context: ClientContext): string[] {
  return context.categories.length > 0 ? context.categories : V3_CATEGORIES;
}

export function buildPrompt(context: ClientContext): string {
  const locale = context.locale.trim() || "en";
  const categories = categoriesFor(context).join(", ");
  return [
    "What is the main object in this photo? Answer in JSON with:",
    "name (the kind of object in 1-4 plain words, never a brand or a person's name),",
    `category (exactly one of: ${categories}),`,
    "tags (up to 3 short lowercase tags for the item),",
    "description (under 12 words, about the object only),",
    "confidence (0 to 1, how sure you are).",
    'brand, model_number, serial_number (copy only text that is printed on the item and clearly readable in this photo; use "" for any of them you cannot read; never guess).',
    `Write the values in the language of locale '${locale}'.`,
  ].join("\n");
}

export function buildSchema(context: ClientContext): Record<string, unknown> {
  return {
    type: "object",
    properties: {
      name: { type: "string", minLength: 2, maxLength: 40 },
      category: { type: "string", enum: categoriesFor(context) },
      tags: { type: "array", items: { type: "string" }, maxItems: 3 },
      description: { type: "string", minLength: 10, maxLength: 100 },
      confidence: { type: "number" },
      brand: { type: "string", maxLength: 30 },
      model_number: { type: "string", maxLength: 30 },
      serial_number: { type: "string", maxLength: 30 },
    },
    required: [
      "name",
      "category",
      "tags",
      "description",
      "confidence",
      "brand",
      "model_number",
      "serial_number",
    ],
  };
}

export const REPAIR_LINE =
  "The previous answer was unusable. Reply again with one JSON object and nothing else.";
