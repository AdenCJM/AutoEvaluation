export const meta = {
  name: 'autoeval-eval',
  description: 'Evaluate the current SKILL.md via Claude Code subagents — no provider API key needed (subscription mode, experimental)',
  whenToUse: 'One evaluation pass of an AutoEvaluation project when no provider API key is configured, or to compare Claude-subagent scoring against API scoring. Not the full optimisation loop.',
  phases: [
    { title: 'Setup', detail: 'read config, skill, and prompt split' },
    { title: 'Generate', detail: 'one subagent per (prompt × replicate), writes sample files' },
    { title: 'Judge', detail: 'fresh-context judge subagent per sample, writes eval files' },
    { title: 'Finalise', detail: 'aggregate and write aggregate.json' },
  ],
}

// Args: { runId?: string, replicates?: number, promptSet?: 'train'|'holdout'|'all' }
// Pass a distinct runId per run (the script cannot generate timestamps):
//   e.g. { runId: 'wf_eval_003' }
const runId = (args && args.runId) || 'wf_eval'
const promptSet = (args && args.promptSet) || 'train'

// ── Phase 1: Setup ───────────────────────────────────────────────────
const SETUP_SCHEMA = {
  type: 'object',
  required: ['skill', 'dimensions', 'prompts', 'replicates'],
  additionalProperties: false,
  properties: {
    skill: { type: 'string' },
    replicates: { type: 'integer' },
    dimensions: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name', 'weight', 'rubric', 'direction'],
        additionalProperties: false,
        properties: {
          name: { type: 'string' },
          weight: { type: 'number' },
          rubric: { type: 'string' },
          direction: { type: 'string' },
        },
      },
    },
    prompts: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'prompt'],
        additionalProperties: false,
        properties: { id: { type: 'string' }, prompt: { type: 'string' } },
      },
    },
  },
}

const setup = await agent(
  `Read these files in the current project root: config.yaml, the skill file its skill_path points to (default SKILL.md), and the prompts JSON its prompts_path points to (default prompts/prompts.json).

Return (as structured output):
- skill: the full skill file content
- replicates: config replicates_per_prompt (default 3)
- dimensions: config llm_judge_dimensions as [{name, weight, rubric, direction}] (direction defaults to "higher_is_better")
- prompts: the "${promptSet}" prompt split as [{id, prompt}]. Split rule: prompts with an explicit "split" field are honoured; of the rest, the LAST max(1, round(n * holdout_fraction)) prompts (config holdout_fraction, default 0.3) are holdout, the earlier ones are train. "all" means every prompt.`,
  { label: 'setup:read-config', phase: 'Setup', schema: SETUP_SCHEMA },
)
if (!setup || !setup.prompts.length) throw new Error('Setup failed: no prompts for set ' + promptSet)

const replicates = (args && args.replicates) || setup.replicates || 3
log(`Evaluating ${setup.prompts.length} ${promptSet} prompts × ${replicates} replicates as run '${runId}'`)

// ── Phases 2+3: Generate then judge, pipelined per job ───────────────
const GEN_SCHEMA = {
  type: 'object', required: ['text'], additionalProperties: false,
  properties: { text: { type: 'string' } },
}

const judgeSchema = (dimensions) => ({
  type: 'object',
  required: dimensions.map(d => d.name),
  additionalProperties: false,
  properties: Object.fromEntries(dimensions.map(d => [d.name, {
    type: 'object',
    required: ['score', 'reason'],
    additionalProperties: false,
    properties: {
      score: { type: 'integer', enum: [1, 2, 3, 4, 5] },
      reason: { type: 'string' },
    },
  }])),
})

const jobs = []
setup.prompts.forEach((p, i) => {
  for (let k = 0; k < replicates; k++) {
    const sampleId = replicates > 1 ? `sample_${i}_${p.id}_r${k}` : `sample_${i}_${p.id}`
    jobs.push({ prompt: p, sampleId })
  }
})

const rubricText = setup.dimensions
  .map((d, i) => `${i + 1}. "${d.name}": ${d.rubric.trim()}`)
  .join('\n\n')

const judged = await pipeline(
  jobs,
  // Generate: the subagent IS the generation model (session's Claude model)
  (job) => agent(
    `You are executing a skill. Follow these instructions exactly as your system instructions:

---SKILL---
${setup.skill}
---END SKILL---

Task: ${job.prompt.prompt}

Write your output (ONLY the task output, nothing else) to the file .tmp/samples/${runId}/${job.sampleId}.txt using the Write tool, and also return it as {text}.`,
    { label: `gen:${job.sampleId}`, phase: 'Generate', schema: GEN_SCHEMA, effort: 'low' },
  ).then(r => r && r.text ? { job, text: r.text } : null),

  // Judge: a fresh subagent context = blind grading for free
  (gen, job) => {
    if (!gen) return null
    return agent(
      `You are a strict, experienced evaluator. Score this output 1-5 on each dimension. Judge quality, not quantity — do NOT reward length.

Dimensions:
${rubricText}

Output to evaluate:
---
${gen.text}
---

After deciding your scores, ALSO write the file .tmp/evals/${runId}/${job.sampleId}_llm_judge.json using the Write tool, containing a JSON object of the form {"<dimension>": {"score": <int 1-5>, "normalised": <(score-1)/4, 4 decimal places>, "reason": "<one sentence>"}} for every dimension. Then return your scores as structured output.`,
      { label: `judge:${job.sampleId}`, phase: 'Judge', schema: judgeSchema(setup.dimensions), effort: 'low' },
    ).then(scores => scores ? { sampleId: job.sampleId, promptId: job.prompt.id, scores } : null)
  },
)

const valid = judged.filter(Boolean)
const failures = jobs.length - valid.length
if (!valid.length) throw new Error('All generation/judge jobs failed')
if (failures) log(`${failures}/${jobs.length} jobs failed and were excluded`)

// ── Aggregate in-script (mirrors tools/score_aggregator.py) ──────────
const norm = s => Math.max(0, Math.min(1, (s - 1) / 4))
const round4 = x => Math.round(x * 10000) / 10000

const perSample = valid.map(v => {
  let composite = 0
  const scores = {}
  for (const d of setup.dimensions) {
    const raw = norm(v.scores[d.name].score)
    scores[d.name] = raw
    const effective = d.direction === 'lower_is_better' ? 1 - raw : raw
    composite += effective * d.weight
  }
  return { sample_id: v.sampleId, prompt_id: v.promptId, scores, composite: round4(composite) }
})

const mean = xs => xs.reduce((a, b) => a + b, 0) / xs.length
const stddev = xs => xs.length > 1
  ? Math.sqrt(xs.map(x => (x - mean(xs)) ** 2).reduce((a, b) => a + b, 0) / (xs.length - 1))
  : 0

const composites = perSample.map(s => s.composite)
const metricAverages = {}
const metricStddev = {}
for (const d of setup.dimensions) {
  const vals = perSample.map(s => s.scores[d.name])
  metricAverages[d.name] = round4(mean(vals))
  metricStddev[d.name] = round4(stddev(vals))
}
const perPrompt = {}
for (const s of perSample) (perPrompt[s.prompt_id] ||= []).push(s.composite)
const perPromptOut = Object.fromEntries(Object.entries(perPrompt).map(
  ([pid, vals]) => [pid, { composite: round4(mean(vals)), n: vals.length }],
))

const aggregate = {
  composite_score: round4(mean(composites)),
  composite_stddev: round4(stddev(composites)),
  metric_averages: metricAverages,
  metric_stddev: metricStddev,
  weights: Object.fromEntries(setup.dimensions.map(d => [d.name, d.weight])),
  directions: Object.fromEntries(setup.dimensions.map(d => [d.name, d.direction])),
  sample_count: perSample.length,
  samples_total: jobs.length,
  judge_errors: failures,
  per_prompt: perPromptOut,
  per_sample: perSample,
  generated_by: 'autoeval-eval workflow (Claude Code subagents — subscription mode)',
}

// ── Phase 4: Finalise ────────────────────────────────────────────────
await agent(
  `Write the file .tmp/evals/${runId}/aggregate.json with exactly this content (pretty-printed JSON):

${JSON.stringify(aggregate, null, 2)}`,
  { label: 'write:aggregate', phase: 'Finalise', effort: 'low' },
)

log(`Composite ${aggregate.composite_score} ± ${aggregate.composite_stddev} → .tmp/evals/${runId}/aggregate.json (compatible with tools/decision.py)`)
return {
  run_id: runId,
  composite_score: aggregate.composite_score,
  composite_stddev: aggregate.composite_stddev,
  per_prompt: perPromptOut,
  judge_errors: failures,
  note: 'Scores come from the session Claude model; do not compare against API-provider runs.',
}
