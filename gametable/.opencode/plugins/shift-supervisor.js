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

const WAKE_PROMPT = `[INTERNAL_SHIFT_HEARTBEAT]
This is an internal GameTable shift wake-up, not a message from the Director.
The Director may be absent and has not said anything new. Do not invent their
words, actions, location, consent, or reaction. Inspect the supported GameLab
state, continue permitted autonomous research if a task exists, and preserve
the distinction between evidence and feelings. You may address the Director
only when you independently have something meaningful to say. Otherwise make
useful progress and keep the outward response concise.`

const FINAL_PROMPT = `[INTERNAL_SHIFT_DEADLINE]
The absolute 180-minute GameTable shift has ended. This is not a message from
the Director. Stop starting new work, inspect the factual and relationship
state, preserve unresolved outcomes honestly, and give your own final words.
Do not invent an employment decision or a reaction from the Director.`

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

  function nextAudienceDelay() {
    audienceSeed = (Math.imul(audienceSeed, 1664525) + 1013904223) >>> 0
    return AUDIENCE_MIN_MS + (audienceSeed % (AUDIENCE_MAX_MS - AUDIENCE_MIN_MS + 1))
  }

  async function relationshipState() {
    try {
      const state = JSON.parse(await readFile(relationshipPath, "utf8"))
      return state?.status === "active" ? state : undefined
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
      await client.session.promptAsync({
        path: { id: activeSessionID },
        body: { parts: [{ type: "text", text }] },
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
      })
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
  }
}
