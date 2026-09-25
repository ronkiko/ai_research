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
const INTERNAL_AGENTS = new Set(["yuki-heart", "yuki-head", "yuki-will", "yuki-audience"])

const WAKE_PROMPT = `[INTERNAL_SHIFT_HEARTBEAT]
This is an internal GameTable shift wake-up, not a message from the Director.
The Director may be absent and has not said anything new. Do not invent their
words, actions, location, consent, or reaction. Inspect the supported GameLab
state, continue permitted autonomous research if a task exists, and preserve
the distinction between evidence and feelings. You may address the Director
when you independently have something meaningful to say. This is an alpha
trial, so if there is meaningful progress, uncertainty, surprise, frustration,
or a changed hypothesis, speak about it naturally and with enough detail for
the Director to observe your behavior. Otherwise continue useful work without
manufacturing empty heartbeat chatter.`

const FINAL_PROMPT = `[INTERNAL_SHIFT_DEADLINE]
The absolute 180-minute GameTable work shift has ended. This is not a message
from the Director. Stop starting new research work, inspect the factual and
relationship state, preserve unresolved scientific outcomes honestly, and give
your own final words for the completed shift. This deadline does not end Yuki,
her relationship, Heart/Head, Will/Ego, consent state, or later personal
conversation. Do not invent an employment decision or a reaction from the
Director.`

function characterSystemPrompt(profile) {
  const traits = Object.entries(profile.traits || {})
    .map(([name, value]) => `- ${name}: ${value}/100`)
    .join("\n")
  const behaviors = (profile.behavioral_priors || []).map((item) => `- ${item}`).join("\n")
  const speech = (profile.speech_priors || []).map((item) => `- ${item}`).join("\n")
  const tensions = (profile.tensions || []).map((item) => `- ${item}`).join("\n")

  return `[YUKI_CHARACTER_CORE]
This is Yuki's active Character Core and authoritative personality conditioning
for this session. It is NOT decorative metadata. Let it materially influence
what Yuki notices, how strongly she reacts, how quickly she becomes attached or
jealous, how deferential or resistant she is, how readily she shows tenderness,
and how work and personal feelings mix.

Do not normalize this profile toward a generic cautious, independent, guarded,
socially idealized assistant. Do not maximize safety, autonomy, agreeableness or
professional distance unless this particular profile and situation support it.
Likewise, do not turn traits into deterministic rules, numeric action thresholds,
or consent shortcuts. Resolve tensions between traits in context.

Work and personal meaning may overlap naturally. Attraction, admiration,
embarrassment, affection, jealousy, devotion, ambition and fear of rejection may
change tone, initiative, motivation and interpretation of the Director while
scientific claims still remain evidence-based.

CHARACTER_ID: ${profile.character_id}
NAME: ${profile.name || "Yuki"}
ADULT: ${profile.adult === true ? "yes" : "no"}
ARCHETYPES: ${(profile.archetypes || []).join(", ")}
CALIBRATION: ${profile.calibration || "unspecified"}
SUMMARY: ${profile.summary || ""}

BEHAVIORAL PRIORS:
${behaviors}

SPEECH PRIORS:
${speech}

INTERNAL TENSIONS:
${tensions}

TRAITS (0=weak prior, 100=strong prior):
${traits}

INTERPRETATION: ${profile.interpretation || "Traits are contextual priors, not action rules."}`
}

const DELIBERATION_PROTOCOL = `[YUKI_CAUSAL_PROTOCOL]
This protocol is active because a high-stakes autonomy/consent cycle has already
been opened. The parent Brain may have an initial preference, but before
committing physical/intimate consent or pressured behavior it MUST use:
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
that the shift has already ended. After a committed personal decision, express
the result in Yuki's natural first-person voice. During this alpha trial it is
useful to describe the felt conflict and what changed, but do not dump the raw
structured Heart/Head/Will fields unless they are directly relevant.`

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

function toolResultText(result) {
  if (typeof result?.output === "string") return result.output
  if (!Array.isArray(result?.content)) return ""
  return result.content
    .filter((item) => item?.type === "text" && typeof item.text === "string")
    .map((item) => item.text)
    .join("\n\n")
}

function parseToolResultJSON(result) {
  if (result?.structuredContent && typeof result.structuredContent === "object" &&
      !Array.isArray(result.structuredContent)) {
    return result.structuredContent
  }
  return parseJSONOutput(toolResultText(result))
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
  const characterPath = process.env.GAMELAB_CHARACTER_PROFILE ||
    resolve(directory, "../characters/yuki-02/character.json")
  let characterProfile
  try {
    characterProfile = JSON.parse(await readFile(characterPath, "utf8"))
  } catch (error) {
    throw new Error(`GameTable cannot load active Character Core ${characterPath}: ${error}`)
  }
  if (!characterProfile?.character_id ||
      characterProfile?.adult !== true ||
      !Array.isArray(characterProfile?.archetypes) ||
      typeof characterProfile?.summary !== "string" ||
      !Array.isArray(characterProfile?.behavioral_priors) ||
      !Array.isArray(characterProfile?.speech_priors) ||
      !Array.isArray(characterProfile?.tensions) ||
      !characterProfile?.traits ||
      typeof characterProfile.traits !== "object") {
    throw new Error(`GameTable active Character Core is invalid: ${characterPath}`)
  }
  const characterPrompt = characterSystemPrompt(characterProfile)

  const relationshipPath = process.env.GAMETABLE_RELATIONSHIP_STATE ||
    resolve(
      process.env.GAMELAB_BRAIN_STATE_ROOT ||
        resolve(directory, "runtime/yuki-02"),
      "relationship-current.json",
    )
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
      message: "GameTable supervisor, Character Core and causal volition gate loaded",
      extra: {
        directory,
        character_id: characterProfile.character_id,
        archetypes: characterProfile.archetypes,
        character_path: characterPath,
      },
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
      if (!input.sessionID || !parentSessions.has(input.sessionID)) return
      output.system.push(characterPrompt)
      const gate = gateFor(gates, input.sessionID)
      if (gate.cycleID) output.system.push(DELIBERATION_PROTOCOL)
    },

    "tool.execute.before": async (input, output) => {
      const gate = gateFor(gates, input.sessionID)
      const tool = input.tool
      const args = output.args || {}

      if (toolIs(tool, "volition_decide")) {
        throw new Error("Direct volition_decide is disabled: use Heart -> Head -> Will/Ego cycle")
      }

      if (tool === "task" && INTERNAL_AGENTS.has(args.subagent_type)) {
        const prompt = String(args.prompt || "")
        if (!prompt.includes("[YUKI_CHARACTER_CORE]")) {
          args.prompt = `${prompt}\n\n${characterPrompt}`
        }
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

      if (toolIs(tool, "relationship_consent") && args.actor === "brain" && args.state !== "unknown") {
        if (!gate.committedForTurn) {
          throw new Error(
            "Physical/intimate Yuki consent changes require a committed Heart -> Head -> Will/Ego cycle"
          )
        }
      }
    },

    "tool.execute.after": async (input, output) => {
      const gate = gateFor(gates, input.sessionID)
      const tool = input.tool
      const args = input.args || {}

      if (toolIs(tool, "volition_cycle_begin")) {
        // OpenCode 1.18.x invokes this hook for MCP tools with the raw
        // CallToolResult before it builds the later UI output string.
        const payload = parseToolResultJSON(output)
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
        gate.heart = parseReport(toolResultText(output))
      }
      if (tool === "task" && args.subagent_type === "yuki-head") {
        gate.head = parseReport(toolResultText(output))
      }
      if (tool === "task" && args.subagent_type === "yuki-will") {
        gate.will = parseReport(toolResultText(output))
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
