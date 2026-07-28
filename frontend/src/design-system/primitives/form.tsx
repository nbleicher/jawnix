import { Children, cloneElement, isValidElement, useId } from "react";
import type { InputHTMLAttributes, ReactNode, Ref, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

import "./form.css";
import { cx } from "./cx";

/** The attributes `Field` injects into whichever control it wraps. */
interface ControlProps {
  id?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
  required?: boolean;
}

export interface FieldProps {
  label: string;
  /** Supplied when the caller needs a stable id (e.g. to focus the control from
   *  a validation summary); otherwise one is generated. */
  id?: string;
  description?: string;
  error?: string;
  required?: boolean;
  children: ReactNode;
}

/**
 * Binds a label, description, and error message to a single form control.
 *
 * The control is cloned rather than rendered through a render-prop so callers
 * write plain JSX (`<Field label="…"><Input /></Field>`) and cannot forget to
 * wire `aria-describedby`/`aria-invalid` — the association is structural.
 */
export function Field({ label, id, description, error, required = false, children }: FieldProps) {
  const generatedId = useId();
  const controlId = id ?? `${generatedId}-control`;
  const descriptionId = `${controlId}-description`;
  const errorId = `${controlId}-error`;

  // Order matters: the description is read before the error, matching reading
  // order on screen.
  const describedBy = cx(description ? descriptionId : null, error ? errorId : null);

  const child = Children.only(children);
  const control = isValidElement<ControlProps>(child)
    ? cloneElement(child, {
        id: controlId,
        ...(describedBy ? { "aria-describedby": describedBy } : {}),
        ...(error ? { "aria-invalid": true } : {}),
        ...(required ? { required: true } : {}),
      })
    : child;

  return (
    <div className="jx-field">
      <label className="jx-field__label" htmlFor={controlId}>
        {label}
        {required ? <span className="jx-field__required"> (required)</span> : null}
      </label>
      {description ? (
        <p className="jx-field__description" id={descriptionId}>
          {description}
        </p>
      ) : null}
      {control}
      {error ? (
        // `role="alert"` announces validation failures as they appear, without
        // moving focus away from the control the user is correcting.
        <p className="jx-field__error" id={errorId} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  ref?: Ref<HTMLInputElement>;
}

export function Input({ className, type = "text", ...rest }: InputProps) {
  return <input type={type} className={cx("jx-input", className)} {...rest} />;
}

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  ref?: Ref<HTMLTextAreaElement>;
}

export function Textarea({ className, rows = 4, ...rest }: TextareaProps) {
  return <textarea rows={rows} className={cx("jx-input", "jx-textarea", className)} {...rest} />;
}

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  ref?: Ref<HTMLSelectElement>;
}

export function Select({ className, ...rest }: SelectProps) {
  return <select className={cx("jx-input", "jx-select", className)} {...rest} />;
}

export interface FieldsetProps {
  legend: string;
  description?: string;
  error?: string;
  children: ReactNode;
}

/** Groups related controls (radio/checkbox sets) under a real <legend>, which is
 *  how assistive technology conveys the group name. */
export function Fieldset({ legend, description, error, children }: FieldsetProps) {
  const generatedId = useId();
  const descriptionId = `${generatedId}-description`;
  const errorId = `${generatedId}-error`;
  const describedBy = cx(description ? descriptionId : null, error ? errorId : null);

  return (
    <fieldset className="jx-fieldset" {...(describedBy ? { "aria-describedby": describedBy } : {})}>
      <legend className="jx-fieldset__legend">{legend}</legend>
      {description ? (
        <p className="jx-field__description" id={descriptionId}>
          {description}
        </p>
      ) : null}
      {children}
      {error ? (
        <p className="jx-field__error" id={errorId} role="alert">
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}
