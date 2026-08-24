import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { SchemaDocument } from "../../../api/schemaReleases";
import { RuntimeSchemaEditor } from "./RuntimeSchemaEditor";

/**
 * The editor writes the schema the platform is answering with, so the two
 * things worth pinning are that it sends the whole document (not just the part
 * it rendered) and that it never invents a checksum.
 */

const ACTIVE: SchemaDocument = {
  configurationReleaseId: "order-discovery-release-abc123",
  configurationChecksum: "f".repeat(64),
  schemaVersion: "2026.08.04",
  fromFile: false,
  document: {
    schema_version: "2026.08.04",
    entities: {
      contact_point: {
        fields: {
          contact_first_name: {
            graph_property: "contact_first_name",
            nullable: true,
            capabilities: { searchable: true, operators: ["EXACT", "PREFIX"] },
          },
        },
      },
    },
  },
};

function renderEditor(overrides: Partial<Parameters<typeof RuntimeSchemaEditor>[0]> = {}) {
  const onSave = vi.fn();
  render(
    <RuntimeSchemaEditor
      active={ACTIVE}
      saving={false}
      error={null}
      onSave={onSave}
      onReload={vi.fn()}
      {...overrides}
    />,
  );
  return { onSave };
}

describe("RuntimeSchemaEditor", () => {
  it("names the release it is editing, so a save is not a guess", () => {
    renderEditor();
    expect(screen.getByText("order-discovery-release-abc123")).toBeInTheDocument();
    expect(screen.getByText("ffffffffffff…")).toBeInTheDocument();
  });

  it("flattens the document to paths an operator can search", async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.type(screen.getByLabelText("Filter fields"), "contact_first_name");

    expect(
      screen.getByTitle("entities.contact_point.fields.contact_first_name.graph_property"),
    ).toBeInTheDocument();
    // The schema is thousands of values; anything not matching must be gone.
    expect(screen.queryByTitle("schema_version")).not.toBeInTheDocument();
  });

  it("sends the whole document, not only the values it rendered", async () => {
    const user = userEvent.setup();
    const { onSave } = renderEditor();

    await user.type(screen.getByLabelText("Filter fields"), "graph_property");
    // The label wraps the path and its control, so the path *is* the field's
    // accessible name.
    const input = screen.getByLabelText(
      "entities.contact_point.fields.contact_first_name.graph_property",
    );
    await user.clear(input);
    await user.type(input, "given_name");
    await user.click(screen.getByRole("button", { name: /Publish and activate/ }));

    expect(onSave).toHaveBeenCalledTimes(1);
    const [document] = onSave.mock.calls[0] as [Record<string, unknown>, boolean];
    // Edited...
    expect(document).toMatchObject({
      entities: {
        contact_point: {
          fields: { contact_first_name: { graph_property: "given_name" } },
        },
      },
    });
    // ...and everything the filter hid is still there. A partial document would
    // publish a release with most of the schema deleted.
    expect(document.schema_version).toBe("2026.08.04");
  });

  it("keeps a boolean a boolean rather than the string 'false'", async () => {
    const user = userEvent.setup();
    const { onSave } = renderEditor();

    await user.type(screen.getByLabelText("Filter fields"), "searchable");
    await user.selectOptions(screen.getByRole("combobox"), "false");
    await user.click(screen.getByRole("button", { name: /Publish/ }));

    const [document] = onSave.mock.calls[0] as [Record<string, unknown>, boolean];
    const capabilities = (
      (
        (document.entities as Record<string, { fields: Record<string, { capabilities: unknown }> }>)
          .contact_point.fields.contact_first_name
      ).capabilities
    ) as { searchable: unknown };
    expect(capabilities.searchable).toBe(false);
  });

  it("will not publish when nothing has been changed", () => {
    renderEditor();
    expect(screen.getByRole("button", { name: /Publish and activate/ })).toBeDisabled();
  });

  it("can publish without pointing the runtime at the result", async () => {
    const user = userEvent.setup();
    const { onSave } = renderEditor();

    await user.type(screen.getByLabelText("Filter fields"), "graph_property");
    await user.type(
      screen.getByLabelText("entities.contact_point.fields.contact_first_name.graph_property"),
      "_v2",
    );
    await user.click(
      screen.getByRole("checkbox", {
        name: /Point the runtime at this release/,
      }),
    );
    await user.click(screen.getByRole("button", { name: /Publish only/ }));

    expect(onSave.mock.calls[0]?.[1]).toBe(false);
  });

  it("discards changes back to what is running", async () => {
    const user = userEvent.setup();
    renderEditor();

    await user.type(screen.getByLabelText("Filter fields"), "graph_property");
    await user.type(
      screen.getByLabelText("entities.contact_point.fields.contact_first_name.graph_property"),
      "_v2",
    );
    expect(screen.getByRole("button", { name: /Publish and activate/ })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(screen.getByRole("button", { name: /Publish and activate/ })).toBeDisabled();
  });

  it("refuses to publish JSON it cannot parse", async () => {
    const user = userEvent.setup();
    const { onSave } = renderEditor();

    await user.click(screen.getByRole("button", { name: "Document" }));
    const editor = screen.getByRole("textbox", { name: /Schema document/ });
    await user.clear(editor);
    // `{{` because userEvent reads a bare `{` as the start of a key descriptor.
    await user.type(editor, "{{ not json");
    await user.click(screen.getByRole("button", { name: /Publish/ }));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("says so when the file is answering by fallback", () => {
    renderEditor({ active: { ...ACTIVE, fromFile: true } });
    expect(screen.getByText(/Nothing has been published yet/)).toBeInTheDocument();
  });

  it("offers a reload when someone else published mid-edit", () => {
    renderEditor({ error: "The schema changed since this edit began." });
    expect(
      screen.getByRole("button", { name: "Reload the current schema" }),
    ).toBeInTheDocument();
  });
});
