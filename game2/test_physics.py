import unittest

from physics import GROUND_Y, PIT_LEFT, PIT_RIGHT, SIZE, SPIKES_Y, World


class PhysicsTests(unittest.TestCase):
    def test_idle_and_vertical_jump(self):
        world = World()
        for _ in range(240):
            self.assertEqual(world.step(), [])
        self.assertEqual(world.y, GROUND_Y - SIZE)
        initial_x = world.x
        world.step(jump=True)
        apex = world.y
        for _ in range(180):
            world.step()
            apex = min(apex, world.y)
        self.assertLess(apex, GROUND_Y - SIZE - 100)
        self.assertEqual(world.x, initial_x)
        self.assertTrue(world.grounded)

    def test_air_control_and_no_double_jump(self):
        world = World()
        world.step(jump=True)
        vy = world.vy
        world.step(right=True, jump=True)
        self.assertGreater(world.vy, vy)
        self.assertGreater(world.x, 100)

    def test_walk_into_pit_emits_one_die(self):
        world = World()
        events = []
        for _ in range(500):
            events.extend(world.step(right=True))
        self.assertEqual([e['event'] for e in events], ['die'])
        self.assertEqual(events[0]['reason'], 'damage_surface')
        self.assertEqual(world.y + SIZE, SPIKES_Y)
        self.assertFalse(world.alive)
        position = (world.x, world.y)
        world.step(right=True, jump=True)
        self.assertEqual((world.x, world.y), position)

    def test_running_jump_crosses_pit(self):
        world = World()
        events = []
        jumped = False
        for _ in range(500):
            jump = not jumped and world.x >= PIT_LEFT - SIZE / 2
            jumped |= jump
            events.extend(world.step(right=True, jump=jump))
        self.assertEqual([e['event'] for e in events], ['success'])
        self.assertTrue(world.alive)
        self.assertTrue(world.grounded)
        self.assertGreaterEqual(world.x, PIT_RIGHT)

    def test_replay_is_deterministic(self):
        a, b = World(), World()
        for tick in range(400):
            self.assertEqual(a.step(tick < 250, tick == 130), b.step(tick < 250, tick == 130))
            self.assertEqual(vars(a), vars(b))


if __name__ == '__main__':
    unittest.main()
