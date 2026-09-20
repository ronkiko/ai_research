"""Canonical training cadence and PPO configuration."""

# Motor reflex decisions run at 60 Hz on the 120 Hz physics clock.
POLICY_STRIDE_TICKS = 2
# The spinal-cord Planner runs at 10 Hz and its MotorPlan stays latched between
# Planner ticks. Motors continue closing the physical feedback loop meanwhile.
PLANNER_STRIDE_TICKS = 12
PPO_TAIL_TICKS = 200

# Stateful button actions need dense credit assignment. Each Motor explicitly
# chooses KEEP/PRESS/RELEASE, so dropping intermediate KEEP decisions can teach
# a lucky stochastic trajectory without teaching the deterministic policy to
# preserve a stable control sequence. Keep every policy decision.
PPO_HISTORY_STRIDE_TICKS = POLICY_STRIDE_TICKS
MAX_EPISODE_DATASETS = 5

# Price of one actual Player -> Controller request. KEEP with no request is free.
CONTROL_REQUEST_PENALTY = 0.005
CONTROL_CHANGE_PENALTY = CONTROL_REQUEST_PENALTY
PPO_GAMMA = 0.99
PPO_GAE_LAMBDA = 0.95
PPO_CLIP_EPS = 0.2
PPO_EPOCHS = 4
PPO_BATCH_SIZE = 64
PPO_ENTROPY_COEF = 0.01
PPO_VALUE_COEF = 0.5
PPO_MAX_GRAD_NORM = 0.5
PPO_LEARNING_RATE = 3e-4
