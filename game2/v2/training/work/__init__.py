"""Single episode-data training path shared by realtime and unpaced runners."""

from .config import (
    CONTROL_CHANGE_PENALTY,
    CONTROL_REQUEST_PENALTY,
    MAX_EPISODE_DATASETS,
    PLANNER_STRIDE_TICKS,
    POLICY_STRIDE_TICKS,
    PPO_BATCH_SIZE,
    PPO_CLIP_EPS,
    PPO_ENTROPY_COEF,
    PPO_DISCOUNT_TICKS,
    PPO_EPOCHS,
    PPO_GAE_LAMBDA,
    PPO_GAMMA,
    PPO_HISTORY_STRIDE_TICKS,
    PPO_LEARNING_RATE,
    PPO_MAX_GRAD_NORM,
    PPO_TAIL_TICKS,
    PPO_VALUE_COEF,
)
from .episode_dataset import (
    DEFAULT_EPISODE_STORE,
    EpisodeDataset,
    EpisodeStep,
    EpisodeStore,
)
from .ppo import EpisodeTrainingResult, train_episode

__all__ = [
    "CONTROL_CHANGE_PENALTY",
    "CONTROL_REQUEST_PENALTY",
    "DEFAULT_EPISODE_STORE",
    "EpisodeDataset",
    "EpisodeStep",
    "EpisodeStore",
    "EpisodeTrainingResult",
    "MAX_EPISODE_DATASETS",
    "PLANNER_STRIDE_TICKS",
    "POLICY_STRIDE_TICKS",
    "PPO_BATCH_SIZE",
    "PPO_CLIP_EPS",
    "PPO_ENTROPY_COEF",
    "PPO_DISCOUNT_TICKS",
    "PPO_EPOCHS",
    "PPO_GAE_LAMBDA",
    "PPO_GAMMA",
    "PPO_HISTORY_STRIDE_TICKS",
    "PPO_LEARNING_RATE",
    "PPO_MAX_GRAD_NORM",
    "PPO_TAIL_TICKS",
    "PPO_VALUE_COEF",
    "train_episode",
]
