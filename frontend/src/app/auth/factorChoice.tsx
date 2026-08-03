import { Link } from "react-router";

import { Fieldset } from "../../design-system/primitives/form";
import { Stack } from "../../design-system/primitives/layout";
import { Text } from "../../design-system/primitives/typography";

export interface FactorChoiceRecord {
  id: string;
  name: string;
  lastUsedAt?: string | null;
}

/* Factor roles never reach the wire — the fixed names ("Jawnix primary",
   "Jawnix replacement") are the only signal of which factor an administrator
   normally uses. */
export function defaultFactorId(factors: FactorChoiceRecord[]): string {
  const primary = factors.find((factor) => factor.name === "Jawnix primary");
  if (primary) return primary.id;

  const replacements = factors.filter(
    (factor) => factor.name === "Jawnix replacement",
  );
  if (replacements.length === 1) return replacements[0]?.id ?? "";

  const mostRecent = factors
    .filter((factor) => factor.lastUsedAt)
    .sort(
      (left, right) =>
        new Date(right.lastUsedAt as string).getTime() -
        new Date(left.lastUsedAt as string).getTime(),
    )[0];
  return mostRecent?.id ?? factors[0]?.id ?? "";
}

export function FactorChooser({
  factors,
  factorId,
  onSelect,
  expanded,
  onExpand,
}: {
  factors: FactorChoiceRecord[];
  factorId: string;
  onSelect: (factorId: string) => void;
  expanded: boolean;
  onExpand: () => void;
}) {
  const selected =
    factors.find((factor) => factor.id === factorId) ?? factors[0];

  if (!expanded) {
    return (
      <Stack gap={2}>
        {selected ? (
          <Stack gap={1}>
            <Text weight="semibold">{selected.name}</Text>
            {selected.lastUsedAt !== undefined ? (
              <Text size="sm" tone="muted">
                Last used{" "}
                {selected.lastUsedAt
                  ? new Date(selected.lastUsedAt).toLocaleString()
                  : "never"}
              </Text>
            ) : null}
          </Stack>
        ) : null}
        {factors.length > 1 ? (
          <div>
            <button
              type="button"
              className="jx-mfa-link-button"
              onClick={onExpand}
            >
              I cannot use my primary authenticator
            </button>
          </div>
        ) : null}
      </Stack>
    );
  }

  return (
    <Fieldset legend="Authenticator to use">
      <Stack gap={3}>
        {factors.map((factor) => (
          <label className="jx-mfa-factor-choice" key={factor.id}>
            <input
              type="radio"
              name="factor"
              value={factor.id}
              checked={factorId === factor.id}
              onChange={() => onSelect(factor.id)}
            />
            <span>
              <Text as="span" weight="semibold">
                {factor.name}
              </Text>
              {factor.lastUsedAt !== undefined ? (
                <Text size="sm" tone="muted">
                  Last used{" "}
                  {factor.lastUsedAt
                    ? new Date(factor.lastUsedAt).toLocaleString()
                    : "never"}
                </Text>
              ) : null}
            </span>
          </label>
        ))}
        <Text size="sm" tone="muted">
          <Link to="/admin/mfa/recover">I cannot use any authenticator</Link>
        </Text>
      </Stack>
    </Fieldset>
  );
}
