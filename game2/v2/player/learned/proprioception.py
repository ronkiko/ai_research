"""Player imports for shared physical sensor normalization."""
from game2.v2.learning.proprioception import (
    HORIZONTAL_SPEED_SCALE, VERTICAL_SPEED_SCALE, BODY_STATE_FEATURES,
    CRITIC_CONTEXT_FEATURES, normalize_velocity_x, normalize_velocity_y,
    body_state_values, body_state_tensor, motion_contact_batch,
    body_state_batch, critic_context_tensor, critic_context_batch,
)
