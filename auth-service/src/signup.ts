import { APIError } from "better-auth/api";

const states = new Set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split(" "));

export function signupProfile(value: unknown) {
  const invalid = () => new APIError("BAD_REQUEST", { message: "Enter your first name, last name, US phone number, and appointed states." });
  if (!value || typeof value !== "object" || Array.isArray(value)) throw invalid();
  const input = value as Record<string, unknown>;
  if (Object.keys(input).some(key => !["first_name", "last_name", "phone", "licensed_states"].includes(key))) throw invalid();
  const first = typeof input.first_name === "string" ? input.first_name.trim() : "";
  const last = typeof input.last_name === "string" ? input.last_name.trim() : "";
  let phone = typeof input.phone === "string" ? input.phone.replace(/\D/g, "") : "";
  if (phone.length === 11 && phone.startsWith("1")) phone = phone.slice(1);
  const chosen = input.licensed_states;
  if (!first || !last || first.length > 120 || last.length > 120 || first.length + last.length + 1 > 160 || phone.length !== 10 || !Array.isArray(chosen) || !chosen.length || chosen.length > 51 || chosen.some(code => typeof code !== "string" || !states.has(code))) throw invalid();
  return { first_name: first, last_name: last, phone, licensed_states: [...new Set(chosen as string[])].sort() };
}
