import type { ComparisonPayload } from "./api";

interface DecisionDiffTableProps {
  comparison: ComparisonPayload;
}

function ruleLabel(ruleHits: string[]): string {
  return ruleHits.length > 0 ? ruleHits.join(", ") : "None";
}

export default function DecisionDiffTable({ comparison }: DecisionDiffTableProps) {
  const rows = [
    {
      label: "Release outcome",
      recorded: comparison.historical.outcome,
      current: comparison.current.outcome,
    },
    {
      label: "Blocking rules",
      recorded: ruleLabel(comparison.historical.rule_hits),
      current: ruleLabel(comparison.current.rule_hits),
    },
    {
      label: "Evidence revision",
      recorded: String(comparison.historical.max_revision),
      current: String(comparison.current.max_revision),
    },
  ];

  return (
    <div className="decision-diff-wrap">
      <table className="decision-diff">
        <caption>
          Recorded snapshot {comparison.historical.snapshot_id} beside a read-only evaluation of
          current evidence.
        </caption>
        <thead>
          <tr>
            <th scope="col">Recorded field</th>
            <th scope="col">Recorded decision</th>
            <th scope="col">Current evidence</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const changed = row.recorded !== row.current;
            return (
              <tr className={changed ? "changed" : "shared"} key={row.label}>
                <th scope="row">
                  <span>{row.label}</span>
                  <small>{changed ? "Changed" : "Unchanged"}</small>
                </th>
                <td>{row.recorded}</td>
                <td>{row.current}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
