import { readFile } from "node:fs/promises"
import { resolve } from "node:path"

const CHECK_MS = Number(process.env.GAMETABLE_HEARTBEAT_CHECK_MS || 5000)
const FIRST_IDLE_MS = Number(process.env.GAMETABLE_FIRST_HEARTBEAT_MS || 45000)
const IDLE_MS = Number(process.env.GAMETABLE_HEARTBEAT_MS || 120000)

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

  async function relationshipState() {
    try {
      const state = JSON.parse(await readFile(relationshipPath, "utf8"))
      return state?.status === "active" ? state : undefined
    } catch {
      return undefined
    }
  }

  async function dispatch(text) {
    if (!activeSessionID || dispatching || !idle) return
    dispatching = true
    idle = false
    syntheticUntil = Date.now() + 15000
    try {
      await client.session.promptAsync({
        path: { id: activeSessionID },
        body: { parts: [{ type: "text", text }] },
      })
      lastWakeAt = Date.now()
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
