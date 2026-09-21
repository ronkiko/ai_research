"""Learned-model normalization for physical Proprioception.

The public sensor carries raw physical measurements. This module is the learned
controller's calibration layer; model normalization never widens the public
sensor capability.
"""
from __future__ import annotations

import math

import torch

from game2.v2.contracts.proprioception import ProprioceptionFrame
from .contracts import MotorPlan


# Current body-model calibration, not map geometry: horizontal max speed and
# vertical jump-speed reference. Keep these aligned with the physical body
# limits whenever a new embodiment changes those limits.
HORIZONTAL_SPEED_SCALE = 340.0
VERTICAL_SPEED_SCALE = 700.0
BODY_STATE_FEATURES = 5
CRITIC_CONTEXT_FEATURES = 9


def normalize_velocity_x(value: float) -> float:
    return math.tanh(float(value) / HORIZONTAL_SPEED_SCALE)


def normalize_velocity_y(value: float) -> float:
    return math.tanh(float(value) / VERTICAL_SPEED_SCALE)


def body_state_values(frame: ProprioceptionFrame) -> tuple[float, ...]:
    if not isinstance(frame, ProprioceptionFrame):
        raise TypeError("body_state_values requires ProprioceptionFrame")
    return (
        normalize_velocity_x(frame.velocity_x),
        normalize_velocity_y(frame.velocity_y),
        float(frame.grounded),
        float(frame.right_pressed),
        float(frame.jump_pressed),
    )


def body_state_tensor(
    frame: ProprioceptionFrame,
    *,
    dtype: torch.dtype = torch.float32,
    device=None,
) -> torch.Tensor:
    return torch.tensor(
        body_state_values(frame), dtype=dtype, device=device
    ).unsqueeze(0)


def motion_contact_batch(
    velocities: torch.Tensor,
    grounded: torch.Tensor,
) -> torch.Tensor:
    if (
        not isinstance(velocities, torch.Tensor)
        or velocities.ndim != 2
        or velocities.shape[1] != 2
    ):
        raise ValueError("velocities must have shape [B,2]")
    if not isinstance(grounded, torch.Tensor) or grounded.ndim != 1:
        raise ValueError("grounded must have shape [B]")
    if len(velocities) != len(grounded):
        raise ValueError("velocity and grounded batch lengths must match")
    vx = torch.tanh(velocities[:, 0] / HORIZONTAL_SPEED_SCALE)
    vy = torch.tanh(velocities[:, 1] / VERTICAL_SPEED_SCALE)
    return torch.stack(
        (
            vx,
            vy,
            grounded.to(dtype=velocities.dtype, device=velocities.device),
        ),
        dim=1,
    )


def body_state_batch(
    velocities: torch.Tensor,
    grounded: torch.Tensor,
    pad_states: torch.Tensor,
) -> torch.Tensor:
    if (
        not isinstance(velocities, torch.Tensor)
        or velocities.ndim != 2
        or velocities.shape[1] != 2
    ):
        raise ValueError("velocities must have shape [B,2]")
    if not isinstance(grounded, torch.Tensor) or grounded.ndim != 1:
        raise ValueError("grounded must have shape [B]")
    if (
        not isinstance(pad_states, torch.Tensor)
        or pad_states.ndim != 2
        or pad_states.shape[1] != 2
    ):
        raise ValueError("pad_states must have shape [B,2]")
    if len(velocities) != len(grounded) or len(velocities) != len(pad_states):
        raise ValueError("Proprioception batch lengths must match")
    physical = motion_contact_batch(velocities, grounded)
    return torch.cat((
        physical,
        pad_states.to(dtype=velocities.dtype, device=velocities.device),
    ), dim=1)


def critic_context_tensor(
    frame: ProprioceptionFrame,
    plan: MotorPlan,
    *,
    dtype: torch.dtype = torch.float32,
    device=None,
) -> torch.Tensor:
    if not isinstance(plan, MotorPlan):
        raise TypeError("critic_context_tensor requires MotorPlan")
    body = body_state_tensor(frame, dtype=dtype, device=device)
    plan_state = torch.tensor(
        [[
            plan.goal.target_dx,
            plan.goal.target_dy,
            float(plan.right_active),
            float(plan.jump_active),
        ]],
        dtype=dtype,
        device=device,
    )
    return torch.cat((body, plan_state), dim=1)


def critic_context_batch(
    body_state: torch.Tensor, plan_state: torch.Tensor
) -> torch.Tensor:
    if (
        not isinstance(body_state, torch.Tensor)
        or body_state.ndim != 2
        or body_state.shape[1] != BODY_STATE_FEATURES
    ):
        raise ValueError("body_state must have shape [B,5]")
    if (
        not isinstance(plan_state, torch.Tensor)
        or plan_state.ndim != 2
        or plan_state.shape[1] != 4
        or len(plan_state) != len(body_state)
    ):
        raise ValueError("plan_state must have shape [B,4]")
    return torch.cat((
        body_state,
        plan_state.to(dtype=body_state.dtype, device=body_state.device),
    ), dim=1)


__all__ = [
    "BODY_STATE_FEATURES",
    "CRITIC_CONTEXT_FEATURES",
    "HORIZONTAL_SPEED_SCALE",
    "VERTICAL_SPEED_SCALE",
    "body_state_batch",
    "body_state_tensor",
    "body_state_values",
    "critic_context_batch",
    "critic_context_tensor",
    "motion_contact_batch",
    "normalize_velocity_x",
    "normalize_velocity_y",
]
