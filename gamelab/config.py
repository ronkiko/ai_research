"""Canonical GameLab v1 experiment configuration."""

HOST_BIND = "127.0.0.1"
HOST_PORT = 17700
DEFAULT_HOST_ID = "game-v1-default"
LAB_HOST_PORT_START = 17701
LAB_HOST_PORT_END = 17732
HOST_PROTOCOL_VERSION = 1
HOST_TIMEOUT = 1.0
HOST_MAX_LINE_BYTES = 1024 * 1024

WORLD_MIN_X = 0.0
WORLD_MAX_X = 1000.0
PLAYER_MAX_SPEED = 180.0
PLAYER_MAX_ACCELERATION = 720.0
PLAYER_DRAG = 4.0
# Compatibility name for calibration code; this is now a limit, not commanded vx.
PLAYER_SPEED = PLAYER_MAX_SPEED
PHYSICS_HZ = 120

MOTOR_HZ = 60
SPINE_HZ = 10
SPINE_PERIOD_MOTOR_STEPS = MOTOR_HZ // SPINE_HZ
HISTORY_FRAMES = 32
SPINE_CHANNELS = 4
# Goal displacement is a local control signal. Scaling it by the whole
# 1000-unit world hid 5..40 unit precision goals in values near zero.
SPINE_GOAL_DISTANCE_SCALE = 40.0
MOTOR_GOAL_SIZE = 4
MOTOR_STATE_SIZE = 2

SUCCESS_TOLERANCE = 0.9
SUCCESS_HOLD_STEPS = 6
DEFAULT_GOAL_TIMEOUT = 10.0
TRAIN_EPISODE_SECONDS = 8.0

PPO_GAMMA = 0.995
PPO_GAE_LAMBDA = 0.95
PPO_CLIP_EPS = 0.2
PPO_EPOCHS = 4
PPO_BATCH_SIZE = 64
PPO_ROLLOUT_STEPS = 256
# Exploration comes from the learned policy variance itself. Do not add an
# entropy bonus that keeps a precision controller noisy after it has evidence
# for a narrower distribution.
PPO_ENTROPY_COEF = 0.0
PPO_VALUE_COEF = 0.5
SPINE_INITIAL_LOG_STD = -1.2
PPO_MAX_GRAD_NORM = 0.5
PPO_LEARNING_RATE = 3e-4

CHECKPOINT_VERSION = 5
MODEL_CONFIGURATION = "spine-cnn4x32-lateststate-desiredvx-policy-v5"
