import { Field, Fieldset, Input, Select, Textarea } from "../../../design-system/primitives/form";
import { Card, Cluster, Stack } from "../../../design-system/primitives/layout";
import type { GallerySection } from "./types";

function Forms() {
  return (
    <Card>
      <Stack gap={4}>
        <Field label="Delivery email" description="Batches are sent here." required>
          <Input type="email" name="email" placeholder="name@example.com" autoComplete="email" />
        </Field>
        <Field label="Quantity" error="Enter a quantity of 500 or fewer.">
          <Input type="number" name="quantity" defaultValue={900} />
        </Field>
        <Field label="Licensed state">
          <Select name="state" defaultValue="">
            <option value="">Choose a state…</option>
            <option value="pa">Pennsylvania</option>
            <option value="nj">New Jersey</option>
          </Select>
        </Field>
        <Field label="Note" description="Required when the disposition is Other.">
          <Textarea name="note" />
        </Field>
        <Fieldset legend="Quality rating" description="Independent of the disposition.">
          <Cluster gap={4}>
            <label>
              <input type="radio" name="quality" value="good" /> Good
            </label>
            <label>
              <input type="radio" name="quality" value="poor" /> Poor
            </label>
          </Cluster>
        </Fieldset>
      </Stack>
    </Card>
  );
}

export default { title: "Forms", order: 40, Component: Forms } satisfies GallerySection;
