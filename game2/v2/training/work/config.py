"""Canonical training cadence and PPO configuration."""

POLICY_STRIDE_TICKS = 2
PPO_TAIL_TICKS = 200

# Stateful button actions need dense credit assignment.  The policy emits
# toggles (RIGHT/JUMP changes), so dropping intermediate KEEP decisions can
# teach a lucky stochastic trajectory without teaching the deterministic
# policy to preserve the control state.  Keep every policy decision.
PPO_HISTORY_STRIDE_TICKS = POLICY_STRIDE_TICKS
MAX_EPISODE_DATASETS = 5

CONTROL_CHANGE_PENALTY = 0.005
PPO_GAMMA = 0.99
PPO_GAE_LAMBDA = 0.95
PPO_CLIP_EPS = 0.2
PPO_EPOCHS = 4
PPO_BATCH_SIZE = 64
PPO_ENTROPY_COEF = 0.01
PPO_VALUE_COEF = 0.5
PPO_MAX_GRAD_NORM = 0.5
PPO_LEARNING_RATE = 3e-4
