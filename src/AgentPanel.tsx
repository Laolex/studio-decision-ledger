// The operator-facing agent, rendered as a transcript rather than a chat log.
//
// Each answer shows three things in order: the tool the agent called, the gate
// that came back, and the model's words. That order is the point. An answer on
// its own would leave a reader unable to tell whether the outcome came from the
// deterministic evaluator or from the model, which is the impression the
// read-only design exists to prevent — so the gate is labelled as the
// evaluator's and the paragraph is labelled as an explanation.
//
// One component serves both placements; only the wrapper class differs.

import { useState } from "react";
import { ArrowRight, Chat, DataBase, WarningFilled } from "@carbon/icons-react";
import { Button, InlineLoading } from "@carbon/react";

import { askAgent, type AgentEvent } from "./api";

export type AgentPlacement = "docked" | "inline";

interface Turn {
  question: string;
  events: AgentEvent[];
}

const SUGGESTIONS = [
  "Can NORTHSTAR-S01E06 be released in NG on 30 July 2026?",
  "What is blocking it?",
];

function ToolCall({ event }: { event: Extract<AgentEvent, { kind: "tool_call" }> }) {
  const args = Object.values(event.args).filter(Boolean).join(" · ");
  return (
    <div className="agent-tool">
      <DataBase size={14} />
      <div>
        <b>{event.name}</b>
        {args && <span>{args}</span>}
      </div>
    </div>
  );
}

function ToolResult({
  event,
}: {
  event: Extract<AgentEvent, { kind: "tool_result" }>;
}) {
  if (event.error) {
    return (
      <div className="agent-gate error">
        <WarningFilled size={14} />
        <div>
          <b>No evidence retrieved</b>
          <span>{event.error}</span>
        </div>
      </div>
    );
  }
  return (
    <div className={`agent-gate ${event.outcome === "AVAILABLE" ? "clear" : "hold"}`}>
      <div>
        <span>Determined by the policy evaluator</span>
        <b>
          {event.outcome}
          {event.rule_hits.length > 0 && ` · ${event.rule_hits.join(", ")}`}
        </b>
        {event.blocking_condition && <p>{event.blocking_condition}</p>}
      </div>
    </div>
  );
}

export default function AgentPanel({ placement }: { placement: AgentPlacement }) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function submit(asked: string) {
    const text = asked.trim();
    if (!text || pending) return;
    setPending(true);
    setError("");
    setQuestion("");
    try {
      const answer = await askAgent(text, sessionId);
      setSessionId(answer.session_id);
      setTurns((previous) => [...previous, { question: text, events: answer.events }]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }

  return (
    <section className={`agent-panel ${placement}`} aria-label="Ask the agent">
      <header className="agent-head">
        <Chat size={16} />
        <div>
          <b>Ask the agent</b>
          <span>Reads bound evidence. It cannot change a decision.</span>
        </div>
      </header>

      <div className="agent-body">
        {turns.length === 0 && !pending && (
          <div className="agent-empty">
            {SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                className="agent-suggestion"
                onClick={() => submit(suggestion)}
              >
                {suggestion}
              </button>
            ))}
          </div>
        )}

        {turns.map((turn, index) => (
          <article className="agent-turn" key={index}>
            <p className="agent-question">{turn.question}</p>
            {turn.events.map((event, position) => {
              if (event.kind === "tool_call")
                return <ToolCall key={position} event={event} />;
              if (event.kind === "tool_result")
                return <ToolResult key={position} event={event} />;
              return (
                <div className="agent-answer" key={position}>
                  <span>Explanation</span>
                  <p>{event.text}</p>
                </div>
              );
            })}
          </article>
        ))}

        {pending && <InlineLoading description="Asking the agent…" />}
        {error && <p className="agent-error">{error}</p>}
      </div>

      <form
        className="agent-form"
        onSubmit={(submitEvent) => {
          submitEvent.preventDefault();
          void submit(question);
        }}
      >
        <input
          value={question}
          onChange={(changeEvent) => setQuestion(changeEvent.target.value)}
          placeholder="Ask about a title, territory and date"
          aria-label="Ask the agent a question"
        />
        <Button
          type="submit"
          size="sm"
          renderIcon={ArrowRight}
          disabled={pending || !question.trim()}
        >
          Ask
        </Button>
      </form>
    </section>
  );
}
