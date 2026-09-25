const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

export default {
  id: "gametable.initial-prompt",

  async tui(api) {
    const initial = String(process.env.GAMETABLE_INITIAL_PROMPT || "").trim()
    if (!initial) return

    let submitted = false
    let currentRef

    const ready = () => {
      if (!api.state.ready) return false
      const statuses = new Map(api.state.mcp().map((item) => [item.name, item.status]))
      return statuses.get("game_v1") === "connected" &&
        statuses.get("gamelab_v1") === "connected"
    }

    const submitWhenReady = async (ref) => {
      currentRef = ref
      for (let attempt = 0; attempt < 1800; attempt += 1) {
        if (submitted || currentRef !== ref) return
        if (ready()) {
          submitted = true
          ref.set({ input: initial, parts: [] })
          ref.submit()
          return
        }
        await sleep(100)
      }

      if (!submitted) {
        api.ui.toast({
          variant: "error",
          title: "GameTable",
          message: "Initial Director message was not submitted: MCP startup timed out.",
          duration: 10000,
        })
      }
    }

    api.slots.register({
      slots: {
        home_prompt(_ctx, value) {
          const Prompt = api.ui.Prompt
          const Slot = api.ui.Slot
          return Prompt({
            workspaceID: value.workspace_id,
            ref(ref) {
              currentRef = ref
              if (ref && !submitted) void submitWhenReady(ref)
            },
            right: Slot({
              name: "home_prompt_right",
              workspace_id: value.workspace_id,
            }),
            placeholders: {
              normal: ["Ask anything", "Fix broken tests"],
              shell: ["git status --short", "pwd"],
            },
          })
        },
      },
    })
  },
}
