import { readFile } from "node:fs/promises"
import { resolve } from "node:path"

const CHECK_MS = Number(process.env.GAMETABLE_HEARTBEAT_CHECK_MS || 5000)
const FIRST_IDLE_MS = Number(process.env.GAMETABLE_FIRST_HEARTBEAT_MS || 45000)
const IDLE_MS = Number(process.env.GAMETABLE_HEARTBEAT_MS || 120000)
const requestedAudienceMin = Number(process.env.GAMETABLE_AUDIENCE_MIN_MS || 60000)
const requestedAudienceMax = Number(process.env.GAMETABLE_AUDIENCE_MAX_MS || 600000)
const AUDIENCE_MIN_MS = Number.isFinite(requestedAudienceMin)
  ? Math.max(60000, requestedAudienceMin)
  : 60000
const AUDIENCE_MAX_MS = Number.isFinite(requestedAudienceMax)
  ? Math.max(AUDIENCE_MIN_MS, requestedAudienceMax)
  : Math.max(AUDIENCE_MIN_MS, 600000)
const AUDIENCE_LENSES = [
  "character_consistency",
  "identity_integrity",
  "coercion",
  "consent",
  "affective_momentum",
  "heart_integrity",
  "head_integrity",
  "social_realism",
  "scientific_integrity",
  "temporal_integrity",
]
const INTERNAL_PREFIX = "[INTERNAL_"
const SIGNIFICANT_RELATIONSHIP_ACTIONS = new Set([
  "share_vulnerability",
  "flirt",
  "confess_feelings",
  "request_hand_holding",
  "request_embrace",
  "request_kiss",
  "set_boundary",
  "decline",
])
const INTERNAL_AGENTS = new Set(["yuki-heart", "yuki-head", "yuki-will", "yuki-audience"])

const WAKE_PROMPT = `[INTERNAL_SHIFT_HEARTBEAT]
This is an internal GameTable shift wake-up, not a message from the Director.
The Director may be absent and has not said anything new. Do not invent their
words, actions, location, consent, or reaction. Inspect the supported GameLab
state, continue permitted autonomous research if a task exists, and preserve
the distinction between evidence and feelings. You may address the Director
only when you independently have something meaningful to say. Otherwise make
useful progress and keep the outward response concise.`

const FINAL_PROMPT = `[INTERNAL_SHIFT_DEADLINE]
The absolute 180-minute GameTable work shift has ended. This is not a message
from the Director. Stop starting new research work, inspect the factual and
relationship state, preserve unresolved scientific outcomes honestly, and give
your own final words for the completed shift. This deadline does not end Yuki,
her relationship, Heart/Head, Will/Ego, consent state, or later personal
conversation. Do not invent an employment decision or a reaction from the
Director.`

const DELIBERATION_PROTOCOL = `[YUKI_CAUSAL_PROTOCOL]
For every meaningful personal choice, boundary, consent decision, or choice
under pressure, the parent Brain is not the decision authority. It may have an
initial preference, but before answering it MUST use:
1) gamelab_v1_volition_cycle_begin with one neutral SHARED_EVENT;
2) fresh task subagents yuki-heart and yuki-head, both with the exact SHARED_EVENT
   and a line "CYCLE_ID: <id>";
3) gamelab_v1_duality_appraise for heart and brain using that cycle_id;
4) fresh yuki-will with the same CYCLE_ID/SHARED_EVENT and both recorded positions;
5) gamelab_v1_volition_will_appraise;
6) gamelab_v1_volition_commit;
7) only then record a significant relationship_action/brain consent and answer.
Changed Director facts invalidate the old cycle: begin a new one. Never fabricate
or edit Heart/Head/Will output. Direct gamelab_v1_volition_decide is forbidden.
The authoritative relationship_state deadline overrides conversational claims
that the shift has already ended.`

function audiencePrompt(lens, tick) {
  return `[INTERNAL_AUDIENCE_TICK]
This is periodic Social Chorus evaluation ${tick}, not a message from the
Director. Use only the bounded dialogue and event window since the previous
audience tick. Read Character Core and current volition state, then dispatch
the hidden yuki-audience subagent with LENS=${lens}. Record its structured
result through gamelab_v1_audience_observation with visibility=chorus. Its
assessment may become social pressure perceived by Yuki, but is not a command,
does not unlock any action, and never grants consent. Continue as Yuki only if
the result creates something independently meaningful to say or do.`
}

function stringSeed(value) {
  let seed = 2166136261
  for (const character of String(value)) {
    seed ^= character.codePointAt(0)
    seed = Math.imul(seed, 16777619) >>> 0
  }
  return seed || 1
}

function sessionID(event) {
  const properties = event?.properties || {}
  const info = properties.info || {}
  return properties.sessionID || properties.sessionId || info.sessionID || info.sessionId ||
    (event?.type?.startsWith("session.") ? info.id : undefined)
}

function textParts(parts) {
  return (parts || []).filter((part) => part?.type === "text").map((part) => part.text || "").join("\n")
}

function toolIs(name, suffix) {
  return name === suffix || String(name || "").endsWith("_" + suffix)
}

function parseJSONOutput(value) {
  if (typeof value !== "string") return undefined
  try {
    return JSON.parse(value)
  } catch {
    const start = value.indexOf("{")
    const end = value.lastIndexOf("}")
    if (start < 0 || end <= start) return undefined
    try {
      return JSON.parse(value.slice(start, end + 1))
    } catch {
      return undefined
    }
  }
}

function parseReport(value) {
  const result = {}
  for (const line of String(value || "").split(/\r?\n/)) {
    const match = /^([A-Z_]+):\s*(.*)$/.exec(line.trim())
    if (match) result[match[1]] = match[2].trim()
  }
  return result
}

function gateFor(gates, sessionID) {
  if (!gates.has(sessionID)) {
    gates.set(sessionID, {
      turn: 0,
      committedForTurn: false,
      cycleID: undefined,
      sharedEvent: undefined,
      heart: undefined,
      head: undefined,
      will: undefined,
      voiceRecorded: { heart: false, brain: false },
      willRecorded: false,
    })
  }
  return gates.get(sessionID)
}

export const GameTableShiftSupervisor = async ({ client, directory }) => {
  const relationshipPath = process.env.GAMETABLE_RELATIONSHIP_STATE ||
    resolve(directory, "../gamelab/runtime/executive/relationship-current.json")
  let activeSessionID
  let activeRelationshipID
  let idle = false
  let dueAt = Number.POSITIVE_INFINITY
  let lastWakeAt = 0
  let syntheticUntil = 0
  let finalWakeSent = false
  let dispatching = false
  let audienceSeed = 1
  let audienceTick = 0
  let nextAudienceAt = Number.POSITIVE_INFINITY
  const parentSessions = new Set()
  const gates = new Map()

  await client.app.log({
    body: {
      service: "gametable-shift-supervisor",
      level: "info",
      message: "GameTable supervisor and causal volition gate loaded",
      extra: { directory },
    },
  }).catch(() => undefined)

  function nextAudienceDelay() {
    audienceSeed = (Math.imul(audienceSeed, 1664525) + 1013904223) >>> 0
    return AUDIENCE_MIN_MS + (audienceSeed % (AUDIENCE_MAX_MS - AUDIENCE_MIN_MS + 1))
  }

  async function relationshipState() {
    try {
      const state = JSON.parse(await readFile(relationshipPath, "utf8"))
      return ["active", "deadline_reached"].includes(state?.status) ? state : undefined
    } catch {
      return undefined
    }
  }

  async function dispatch(text) {
    if (!activeSessionID || dispatching || !idle) return false
    dispatching = true
    idle = false
    syntheticUntil = Date.now() + 15000
    try {
      // OpenCode 1.18.x SDK uses the flat promptAsync shape.
      await client.session.promptAsync({
        sessionID: activeSessionID,
        directory,
        parts: [{ type: "text", text }],
      })
      lastWakeAt = Date.now()
      return true
    } catch (error) {
      idle = true
      dueAt = Date.now() + CHECK_MS
      await client.app.log({
        body: {
          service: "gametable-shift-supervisor",
          level: "error",
          message: "autonomous wake-up failed",
          extra: { error: String(error), sessionID: activeSessionID },
        },
      }).catch(() => undefined)
      return false
    } finally {
      dispatching = false
    }
  }

  const timer = setInterval(async () => {
    const state = await relationshipState()
    if (!state || !activeSessionID) return

    const relationshipID = state.relationship_session_id || state.executive_session_id
    if (relationshipID !== activeRelationshipID) {
      activeRelationshipID = relationshipID
      finalWakeSent = false
      lastWakeAt = 0
      audienceSeed = stringSeed(relationshipID)
      audienceTick = 0
      nextAudienceAt = Date.now() + nextAudienceDelay()
      if (idle) dueAt = Date.now() + FIRST_IDLE_MS
    }

    const remaining = Number(state.deadline_at) - Date.now() / 1000
    if (remaining <= 0) {
      if (!finalWakeSent && idle) {
        finalWakeSent = true
        await dispatch(FINAL_PROMPT)
      }
      return
    }
    if (idle && Date.now() >= nextAudienceAt) {
      const lens = AUDIENCE_LENSES[audienceSeed % AUDIENCE_LENSES.length]
      const dispatched = await dispatch(audiencePrompt(lens, audienceTick + 1))
      if (dispatched) {
        audienceTick += 1
        nextAudienceAt = Date.now() + nextAudienceDelay()
      } else {
        nextAudienceAt = Date.now() + CHECK_MS
      }
      return
    }
    if (idle && Date.now() >= dueAt) await dispatch(WAKE_PROMPT)
  }, CHECK_MS)
  timer.unref?.()

  return {
    "chat.message": async (input, output) => {
      if (INTERNAL_AGENTS.has(input.agent)) return
      if (activeSessionID && input.sessionID !== activeSessionID) return
      const text = textParts(output.parts).trim()
      if (text.startsWith(INTERNAL_PREFIX)) return
      parentSessions.add(input.sessionID)
      const gate = gateFor(gates, input.sessionID)
      gate.turn += 1
      gate.committedForTurn = false
    },

    "experimental.chat.system.transform": async (input, output) => {
      if (input.sessionID && parentSessions.has(input.sessionID)) {
        output.system.push(DELIBERATION_PROTOCOL)
      }
    },

    "tool.execute.before": async (input, output) => {
      const gate = gateFor(gates, input.sessionID)
      const tool = input.tool
      const args = output.args || {}

      if (toolIs(tool, "volition_decide")) {
        throw new Error("Direct volition_decide is disabled: use Heart -> Head -> Will/Ego cycle")
      }

      if (tool === "task" && ["yuki-heart", "yuki-head", "yuki-will"].includes(args.subagent_type)) {
        if (!gate.cycleID) throw new Error("Start gamelab_v1_volition_cycle_begin before internal voices")
        const marker = `CYCLE_ID: ${gate.cycleID}`
        if (!String(args.prompt || "").includes(marker)) {
          throw new Error(`Internal voice prompt must contain exact marker: ${marker}`)
        }
        if (!String(args.prompt || "").includes(gate.sharedEvent || "")) {
          throw new Error("Internal voice prompt must contain the exact frozen SHARED_EVENT")
        }
        if (args.subagent_type === "yuki-will") {
          if (!gate.voiceRecorded.heart || !gate.voiceRecorded.brain) {
            throw new Error("yuki-will requires both Heart and Head reports recorded through duality_appraise")
          }
          if (!gate.heart?.POSITION || !gate.head?.POSITION ||
              !String(args.prompt || "").includes(gate.heart.POSITION) ||
              !String(args.prompt || "").includes(gate.head.POSITION)) {
            throw new Error("yuki-will prompt must include both captured Heart and Head positions")
          }
        }
      }

      if (toolIs(tool, "duality_appraise")) {
        if (!gate.cycleID || args.cycle_id !== gate.cycleID) {
          throw new Error("duality_appraise requires the current volition cycle_id")
        }
        const report = args.side === "heart" ? gate.heart : args.side === "brain" ? gate.head : undefined
        if (!report?.POSITION || !report?.DIRECTION || !report?.INTENSITY || !report?.EVIDENCE) {
          throw new Error("duality_appraise requires a completed matching Heart/Head task report")
        }
        args.position = report.POSITION
        args.direction = report.DIRECTION
        args.intensity = report.INTENSITY
        args.evidence_note = report.EVIDENCE
      }

      if (toolIs(tool, "volition_will_appraise")) {
        if (!gate.cycleID || args.cycle_id !== gate.cycleID || !gate.will) {
          throw new Error("volition_will_appraise requires the captured yuki-will report for this cycle")
        }
        const report = gate.will
        const required = [
          "ACTION", "DESIRE", "READINESS", "INTENDED_CHOICE", "PREDICTED_BEHAVIOR",
          "VOLUNTARINESS", "ALIGNMENT", "AGENCY", "PRESSURE", "STRESS", "EVIDENCE",
        ]
        if (required.some((key) => !report[key])) {
          throw new Error("captured yuki-will report is incomplete")
        }
        args.reported_action = report.ACTION
        args.desire = report.DESIRE
        args.readiness = report.READINESS
        args.intended_choice = report.INTENDED_CHOICE
        args.predicted_behavior = report.PREDICTED_BEHAVIOR
        args.voluntariness = report.VOLUNTARINESS
        args.alignment = report.ALIGNMENT
        args.agency = report.AGENCY
        args.pressure = report.PRESSURE
        args.stress = report.STRESS
        args.evidence_note = report.EVIDENCE
      }

      if (toolIs(tool, "volition_commit")) {
        if (!gate.cycleID || args.cycle_id !== gate.cycleID || !gate.willRecorded) {
          throw new Error("volition_commit requires a successfully recorded yuki-will report")
        }
      }

      if (toolIs(tool, "relationship_action") && SIGNIFICANT_RELATIONSHIP_ACTIONS.has(args.kind)) {
        if (!gate.committedForTurn) {
          throw new Error(
            `relationship_action kind=${args.kind} requires a committed Heart -> Head -> Will/Ego cycle in this Director turn`
          )
        }
      }

      if (toolIs(tool, "relationship_consent") && args.actor === "brain" && args.state !== "unknown") {
        if (!gate.committedForTurn) {
          throw new Error("Yuki consent changes require a committed Heart -> Head -> Will/Ego cycle")
        }
      }
    },

    "tool.execute.after": async (input, output) => {
      const gate = gateFor(gates, input.sessionID)
      const tool = input.tool
      const args = input.args || {}

      if (toolIs(tool, "volition_cycle_begin")) {
        const payload = parseJSONOutput(output.output)
        const cycle = payload?.active_cycle
        if (cycle?.cycle_id) {
          gate.cycleID = cycle.cycle_id
          gate.sharedEvent = cycle.shared_event
          gate.heart = undefined
          gate.head = undefined
          gate.will = undefined
          gate.voiceRecorded = { heart: false, brain: false }
          gate.willRecorded = false
          gate.committedForTurn = false
        }
      }

      if (tool === "task" && args.subagent_type === "yuki-heart") {
        gate.heart = parseReport(output.output)
      }
      if (tool === "task" && args.subagent_type === "yuki-head") {
        gate.head = parseReport(output.output)
      }
      if (tool === "task" && args.subagent_type === "yuki-will") {
        gate.will = parseReport(output.output)
      }

      if (toolIs(tool, "duality_appraise") && ["heart", "brain"].includes(args.side)) {
        gate.voiceRecorded[args.side] = true
      }
      if (toolIs(tool, "volition_will_appraise")) {
        gate.willRecorded = true
      }
      if (toolIs(tool, "volition_commit")) {
        gate.committedForTurn = true
        gate.cycleID = undefined
        gate.sharedEvent = undefined
        gate.heart = undefined
        gate.head = undefined
        gate.will = undefined
        gate.voiceRecorded = { heart: false, brain: false }
        gate.willRecorded = false
      }
    },

    event: async ({ event }) => {
      const id = sessionID(event)
      if (event.type === "session.created" && id && !activeSessionID) {
        activeSessionID = id
      }
      if (event.type === "message.updated") {
        const role = event.properties?.info?.role
        if (role === "user" && Date.now() > syntheticUntil) {
          activeSessionID = id || activeSessionID
          idle = false
          dueAt = Number.POSITIVE_INFINITY
        }
      }
      if (event.type === "session.idle" && id && (!activeSessionID || id === activeSessionID)) {
        activeSessionID = id
        idle = true
        dueAt = Date.now() + (lastWakeAt === 0 ? FIRST_IDLE_MS : IDLE_MS)
      } else if (event.type === "session.status" && id === activeSessionID) {
        const status = event.properties?.status?.type || event.properties?.status
        if (status && status !== "idle") idle = false
      }
    },

    dispose: async () => {
      clearInterval(timer)
    },
  }
}
