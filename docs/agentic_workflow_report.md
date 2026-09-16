<!--docmeta
title: Agentic Workflow for Spec-to-Silicon: Research Report
genre: overview
status: active
area: top
owner: soumyajit
updated: 2026-09-16
summary: A comprehensive report detailing the potential benefits, pitfalls, and implementation options for transitioning the spec2si headless ASIC design flow from interactive LLM sessions to a fully agentic workflow.
-->

# Agentic Workflow for Spec-to-Silicon: Research Report

**Executive Summary**
The `spec2si` repositories currently employ a headless, code-driven flow across multiple process nodes (TSMC 65nm, TSMC 28nm, X-FAB XT011, and SkyWater SKY130). Thus far, the integration with large language models (LLMs) has been interactive and human-driven via desktop or command-line interfaces to commercial models such as OpenAI GPT-5 and 6 and Claude Sonnet / Opus / Fable. Transitioning to an *agentic* workflow means shifting from AI-as-an-advisor to AI-as-an-executor, where autonomous or semi-autonomous agents chain tools, reason about feedback (like DRC/LVS/IR-drop results), and iterate directly in the design environment.

## 1. Potential Benefits and Pitfalls

### Potential Benefits
1. **End-to-End Automation of the Verification Loop**: The DRC repair loop (as seen in `spec2si-flowkit/drcloop`) and routing iterations currently rely heavily on deterministic scripts, but corner cases often require human intervention. Agents can autonomously parse `drc.out` logs or Pegasus results, trace the error to a specific rule violation (e.g., M7 spacing or DATATYPE gating), and generate code or layout modifications to fix it.
2. **Accelerated Porting and Bring-Up**: Adding a node (e.g., the ongoing `spec2si-sky130` bring-up) involves translating policies and bridging capability gaps. Agents equipped with PDK documentation can autonomously generate the mapping (e.g., `analog/specs/flow_policy.json`) and stub out rules engine bindings.
3. **Cross-Repo Consistency Enforcement**: `spec2si-flowkit` serves as the node-agnostic core. An agentic workflow can monitor divergence, run the conformance suite (`sync.py --check-all`), and proactively open PRs to fix drift across `tsmc65`, `tsmc28`, `xt011`, and `sky130` when rules change.
4. **Enhanced Design-Space Exploration**: Instead of manually tuning routing grids or wire widths, an agent can drive the IR-drop solver (`irdrop/solver.py`) in a loop, iteratively widening bands (`elec.solve_width`) until EM constraints and voltage drops meet the specifications defined in `flow_policy.core.json`.

### Pitfalls and Risks
1. **Loss of Determinism and Trust**: ASIC design is highly intolerant of non-deterministic behavior. A hallucinated PDK rule interpretation by an agent could silently introduce a yield-killing defect. Any agentic output must be tightly guarded by the existing "tool-verified" deterministic gates (like Calibre or Pegasus).
2. **Context Fragmentation**: The flow is split into a core (`spec2si-flowkit`) and proprietary PDK repos. If an agent lacks the context of why a certain divergence exists (e.g., why Pegasus is used instead of Calibre for one node), it might attempt to erroneously "correct" valid divergence, breaking the node-specific engine.
3. **Infinite Loops and Cost Overruns**: Agents attempting to close DRC loops can easily fall into infinite iterations—fixing a spacing violation that creates a density violation, and then reverting the fix. This requires strict iteration boundaries and state-tracking.
4. **NDAs and Proprietary Data**: Process nodes (TSMC, X-FAB) are covered by strict NDAs. Using cloud-based agentic workflows requires careful credential and data-leakage management, ensuring PDK rules and geometries are not sent to public cloud endpoints.

## 2. Implementation Options

To move from interactive "copilot" sessions to an agentic workflow for `spec2si`, there are several implementation paths:

### Option A: The "Tool-Armed" Scripting Agent (Near-Term)
Instead of a full autonomous agent, implement a Python-based CLI agent (using frameworks like LangChain, AutoGen, or smolagents) that runs directly on your local system or secure cluster.
* **How it works**: The agent is provided with strict tools: `run_conformance_test()`, `read_drc_log()`, `query_docmodel()`, and `edit_file()`.
* **Use Case**: You instruct the agent: "Fix the M7 DATATYPE gating rule in the tsmc28 port." The agent reads `flow_policy.core.json`, reads the tsmc28 routing card, makes the edit, and runs `python3 sync.py` and `python3 docs/gen.py check`. If it fails, it reads the error and iterates.
* **Pros**: Low barrier to entry; uses existing standard libraries; highly controlled.
* **Cons**: Still requires you to dispatch individual, bounded tasks.

### Option B: The "Supervisor-Worker" Multi-Agent Architecture (Mid-Term)
In this model, you deploy specialized agents for different domains of the `spec2si` flow.
* **Architecture**:
  * **Planning/Supervisor Agent**: Reads `RESUME.md` or `docs/routekit_plan.md` to understand the state of the repositories and orchestrates tasks.
  * **DRC/LVS Agent**: Specializes in reading Calibre/Pegasus reports and repairing layout violations.
  * **Policy/Core Agent**: Focuses strictly on `spec2si-flowkit` and maintaining the vendor sync across process nodes.
* **Pros**: Separation of concerns prevents the agent from losing context. The Planning Agent can manage the complex, multi-step plans found in the docs.
* **Cons**: Requires building and tuning a multi-agent orchestration layer (e.g. using LangGraph or CrewAI).

### Option C: Integration with Emerging EDA Agent Platforms (Long-Term)
As EDA vendors roll out their own AI infrastructure (e.g., Synopsys AgentEngineer, Cadence ChipStack), you can interface the headless flow directly with these platforms via their APIs.
* **How it works**: Instead of building the agent yourself, you write an adapter layer where `spec2si-flowkit` queries the EDA vendor's agent to perform specific optimizations (like advanced macro placement or automated DRC-clean routing).
* **Pros**: Leverages billions of dollars in EDA R&D; inherently understands the PDKs.
* **Cons**: Vendor lock-in; moves away from the "headless, code-driven" philosophy of `spec2si` if the vendor agents are opaque.

## Recommended Next Steps for `spec2si`
1. **Agentic "Memory" Integration**: Proceed with Phase 0 of the existing `docs/agent_memory_plan.md`. Create a localized vector database or structured context injection pipeline so the agent can query past runlogs, session artifacts, and conformance results without needing a human to paste them in.
2. **The DRC Agent Proof-of-Concept**: Wrap the `drcloop` module so a local agent can intercept Calibre/Pegasus markers, propose a fix via Python script generation, and automatically submit it to the `verify_layout` queue. Use the existing `--score-only` dry-runs to gate the agent safely.
