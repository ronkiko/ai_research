import unittest
from dataclasses import asdict

from game import GameContainer
from controls import Action

# Expected geometry of maps/pit.json; the engine has no such constants.
GROUND_Y, PIT_LEFT, PIT_RIGHT, SIZE, SPIKES_Y = 448, 512, 768, 64, 640
from physics import Body, PhysicsConfig, PhysicsWorld, Surface


class PhysicsTests(unittest.TestCase):
    def flat_world(self, **kwargs):
        return PhysicsWorld(Body(100, 296, **kwargs), [Surface(-1000, 360, 10000, 100)])

    def test_idle_stable(self):
        world = self.flat_world()
        for _ in range(1000):
            self.assertEqual(world.step(), [])
            self.assertEqual((world.body.x, world.body.y, world.body.vy), (100, 296, 0))
            self.assertTrue(world.body.grounded)

    def test_standing_jump_then_right_does_not_move_in_air(self):
        world = self.flat_world()
        world.step(jump=True)
        apex = world.body.y
        for _ in range(150):
            world.step(move=1)
            apex = min(apex, world.body.y)
            self.assertEqual((world.body.x, world.body.vx), (100, 0))
            if world.body.grounded:
                break
        self.assertTrue(world.body.grounded)
        self.assertLess(apex, 196)
        world.step(move=1)
        self.assertGreater(world.body.x, 100)  # Input starts working after landing.

    def test_simultaneous_right_and_jump_from_rest_has_no_runup(self):
        world = self.flat_world()
        world.step(move=1, jump=True)
        self.assertEqual(world.body.vx, 0)
        self.assertEqual(world.body.x, 100)
        self.assertLess(world.body.vy, 0)

    def test_no_double_jump(self):
        world = self.flat_world()
        world.step(jump=True)
        vy = world.body.vy
        world.step(jump=True)
        self.assertAlmostEqual(world.body.vy, vy + world.config.gravity * world.config.dt)

    def test_runup_acceleration_and_cap(self):
        world = self.flat_world()
        world.step(move=1)
        self.assertGreater(world.body.vx, 0)
        self.assertLess(world.body.vx, world.config.max_speed)
        for _ in range(100):
            world.step(move=1)
        self.assertEqual(world.body.vx, world.config.max_speed)

    def test_braking_distance_and_time(self):
        world = self.flat_world(vx=340)
        start_x = world.body.x
        velocities = []
        for _ in range(120):
            world.step()
            velocities.append(world.body.vx)
        # Continuous reference v²/(2a)=41.286 px; fixed step error <= v*dt.
        distance = world.body.x - start_x
        expected = 340 ** 2 / (2 * world.config.braking)
        self.assertGreater(distance, 0)
        self.assertAlmostEqual(distance, expected, delta=340 * world.config.dt)
        self.assertEqual(world.body.vx, 0)
        self.assertEqual(velocities, sorted(velocities, reverse=True))
        stop_ticks = next(i + 1 for i, v in enumerate(velocities) if v == 0)
        self.assertAlmostEqual(stop_ticks * world.config.dt, 340 / 1400, delta=world.config.dt)

    def test_air_trajectory_independent_of_buttons(self):
        a, b = self.flat_world(), self.flat_world()
        for _ in range(40):
            a.step(move=1)
            b.step(move=1)
        takeoff_speed = a.body.vx
        a.step(move=1, jump=True)
        b.step(move=0, jump=True)
        for tick in range(150):
            self.assertEqual(asdict(a.body), asdict(b.body))
            self.assertEqual(a.body.vx, takeoff_speed)
            if a.body.grounded:
                break
            a.step(move=1, jump=tick == 10)
            b.step(move=-1 if tick % 2 else 0)
        self.assertTrue(a.body.grounded)

    def test_jump_after_landing_is_allowed(self):
        world = self.flat_world()
        world.step(jump=True)
        for _ in range(150):
            world.step()
            if world.body.grounded:
                break
        self.assertTrue(world.body.grounded)
        world.step(jump=True)
        self.assertFalse(world.body.grounded)
        self.assertLess(world.body.vy, 0)

    def test_walking_off_edge_does_not_allow_jump(self):
        world = PhysicsWorld(Body(99, 36, vx=340), [Surface(0, 100, 100, 20)])
        world.step(move=1)
        self.assertFalse(world.body.grounded)
        vx = world.body.vx
        world.step(move=1, jump=True)
        self.assertGreater(world.body.vy, 0)
        self.assertEqual(world.body.vx, vx)

    def test_thin_floor_cannot_be_tunnelled(self):
        world = PhysicsWorld(Body(100, 0, vy=30000), [Surface(0, 100, 1000, 1)])
        world.step()
        self.assertEqual(world.body.y, 36)
        self.assertEqual(world.body.vy, 0)
        self.assertTrue(world.body.grounded)

    def test_thin_walls_both_directions(self):
        for vx, wall, expected in [(30000, Surface(100, -1000, 1, 3000), 36),
                                   (-30000, Surface(-100, -1000, 1, 3000), -99)]:
            with self.subTest(vx=vx):
                world = PhysicsWorld(Body(0, 0, vx=vx), [wall])
                world.step()
                self.assertEqual(world.body.x, expected)
                self.assertEqual(world.body.vx, 0)
                self.assertGreater(world.body.y, 0)  # Continue falling alongside wall.

    def test_ceiling_stops_upward_velocity(self):
        world = PhysicsWorld(Body(0, 100, vy=-30000), [Surface(-100, 50, 1000, 1)])
        world.step()
        self.assertEqual(world.body.y, 51)
        self.assertEqual(world.body.vy, 0)
        self.assertFalse(world.body.grounded)
        world.step()
        self.assertGreater(world.body.y, 51)

    def test_damage_contact_from_any_direction(self):
        scenarios = [
            (Body(0, 0, vy=30000), Surface(-100, 100, 1000, 1, True)),
            (Body(0, 0, vx=30000), Surface(100, -1000, 1, 3000, True)),
            (Body(0, 0, vx=-30000), Surface(-100, -1000, 1, 3000, True)),
            (Body(0, 100, vy=-30000), Surface(-100, 50, 1000, 1, True)),
        ]
        for body, hazard in scenarios:
            with self.subTest(hazard=hazard):
                world = PhysicsWorld(body, [hazard])
                self.assertEqual(world.step(), [{'event': 'die', 'reason': 'damage_surface', 'tick': 1}])
                frozen = asdict(body)
                for _ in range(10):
                    self.assertEqual(world.step(move=1, jump=True), [])
                    self.assertEqual(asdict(body), frozen)

    def test_nearest_collision_wins_independent_of_surface_order(self):
        floor = Surface(-100, 100, 1000, 1)
        hazard = Surface(-100, 200, 1000, 1, True)
        for surfaces in [(floor, hazard), (hazard, floor)]:
            world = PhysicsWorld(Body(0, 0, vy=30000), surfaces)
            self.assertEqual(world.step(), [])
            self.assertEqual(world.body.y, 36)
            self.assertTrue(world.body.alive)

    def test_corner_contact_and_surface_order(self):
        floor = Surface(-100, 100, 1000, 1)
        wall = Surface(100, -100, 1, 1000)
        for surfaces in [(floor, wall), (wall, floor)]:
            world = PhysicsWorld(Body(0, 0, vx=12000, vy=11985), surfaces)
            world.step()
            self.assertEqual((world.body.x, world.body.y), (36, 36))
            self.assertEqual((world.body.vx, world.body.vy), (0, 0))

    def test_spawn_and_parameters_validation(self):
        with self.assertRaises(ValueError):
            PhysicsWorld(Body(0, 0), [Surface(0, 0, 100, 100)])
        with self.assertRaises(ValueError):
            PhysicsConfig(hz=0)
        world = PhysicsWorld(Body(0, 0), [])
        with self.assertRaises(ValueError):
            world.step(move=2)


class GameTests(unittest.TestCase):
    def new_game(self):
        game = GameContainer(mode='mlp', port=0)
        self.addCleanup(game.close)
        return game

    def test_walk_into_pit_emits_one_die(self):
        game = self.new_game()
        events = []
        for _ in range(500):
            events.extend(game.step(Action(True)))
        self.assertEqual([e['event'] for e in events], ['die'])
        self.assertEqual(game.body.y + SIZE, SPIKES_Y)
        self.assertFalse(game.body.alive)

    def test_running_jump_crosses_pit(self):
        game = self.new_game()
        events, jumped = [], False
        for _ in range(500):
            jump = not jumped and game.body.x >= PIT_LEFT - SIZE / 2
            jumped |= jump
            events.extend(game.step(Action(True, jump)))
        self.assertEqual([e['event'] for e in events], ['success'])
        self.assertTrue(game.body.alive)
        self.assertTrue(game.body.grounded)
        self.assertGreaterEqual(game.body.x, PIT_RIGHT)
        self.assertEqual(game.body.y, GROUND_Y - SIZE)

    def test_early_jump_fails(self):
        game = self.new_game()
        events, jumped = [], False
        for _ in range(500):
            jump = not jumped and game.body.x >= 300
            jumped |= jump
            events.extend(game.step(Action(True, jump)))
        self.assertEqual([e['event'] for e in events], ['die'])

    def test_replay_independent_of_render_batch_size(self):
        # Same tick-indexed input tape with render every 1, 2, 4, or 7 ticks.
        actions = [(tick < 250, tick == 140) for tick in range(420)]
        traces = []
        for batch_size in (1, 2, 4, 7):
            game, trace = self.new_game(), []
            for offset in range(0, len(actions), batch_size):
                for right, jump in actions[offset:offset + batch_size]:
                    events = game.step(Action(right, jump))
                    trace.append((asdict(game.body), events, (game.status == 2)))
            traces.append(trace)
        for trace in traces[1:]:
            self.assertEqual(trace, traces[0])

    def test_restart_is_fresh(self):
        game = self.new_game()
        for _ in range(500):
            game.step(Action(True))
        self.assertFalse(game.body.alive)
        game = self.new_game()
        self.assertTrue(game.body.alive)
        self.assertTrue(game.body.grounded)
        self.assertFalse((game.status == 2))
        self.assertEqual(game.physics.tick, 0)


if __name__ == '__main__':
    unittest.main()
