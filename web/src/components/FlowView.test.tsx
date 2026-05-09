import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AskResponse } from "../api/types";

import { FlowView } from "./FlowView";

function noop(): void {}

function makeResponse(overrides: Partial<AskResponse> = {}): AskResponse {
  return {
    text: "Sample answer",
    persona: "owner",
    service_region: null,
    tools_called: ["/customers"],
    guardrail_blocks: 0,
    duration_seconds: 4.5,
    session_id: "gagent-owner-abc123",
    ...overrides,
  };
}

describe("FlowView", () => {
  it("renders all nodes in the idle state when response is null", () => {
    render(
      <FlowView
        role="owner"
        serviceRegion={null}
        onChangePersona={noop}
        response={null}
      />,
    );

    expect(screen.getByTestId("flow-idle-hint")).toBeInTheDocument();

    const nodes = screen.getAllByTestId(/^flow-node-/);
    expect(nodes.length).toBeGreaterThanOrEqual(11);
    for (const node of nodes) {
      expect(node).toHaveAttribute("data-state", "idle");
    }
    expect(screen.queryByTestId("flow-footer")).toBeNull();
  });

  it("populates badges from a successful response", () => {
    render(
      <FlowView
        role="owner"
        serviceRegion={null}
        onChangePersona={noop}
        response={makeResponse()}
      />,
    );

    expect(screen.queryByTestId("flow-idle-hint")).toBeNull();

    // Bedrock Agent node shows total duration.
    const bedrock = screen.getByTestId("flow-badge-bedrock-agent");
    expect(bedrock).toHaveTextContent("4.50s");

    // Action-group node shows tool count + the path.
    const actionGroup = screen.getByTestId(
      "flow-badge-action-group-lambda-governed-query",
    );
    expect(actionGroup).toHaveTextContent("1 tool call");
    expect(actionGroup).toHaveTextContent("/customers");

    // Guardrails node shows the green "0 blocks" badge.
    const guardrails = screen.getByTestId("flow-badge-bedrock-guardrails");
    expect(guardrails).toHaveTextContent("0 blocks");

    // Footer with the CloudWatch Insights query block is rendered.
    expect(screen.getByTestId("flow-footer")).toBeInTheDocument();
    expect(screen.getByTestId("flow-footer")).toHaveTextContent(
      "gagent-owner-abc123",
    );
  });

  it("flips the guardrails badge tone when blocks > 0", () => {
    render(
      <FlowView
        role="dispatcher"
        serviceRegion={null}
        onChangePersona={noop}
        response={makeResponse({ persona: "dispatcher", guardrail_blocks: 2 })}
      />,
    );

    const guardrails = screen.getByTestId("flow-badge-bedrock-guardrails");
    expect(guardrails).toHaveTextContent("2 blocks");

    // Guardrails container picks up the warning tone (amber bg).
    const guardrailsNode = screen.getByTestId("flow-node-bedrock-guardrails");
    expect(guardrailsNode.className).toMatch(/bg-amber-50/);
  });
});
