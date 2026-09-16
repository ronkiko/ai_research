import json
import tempfile
import unittest
from pathlib import Path

from level import DEFAULT_MAP, TILE_SIZE, load_level
from monitors import ColorRenderer


class TileTests(unittest.TestCase):
    def test_every_cell_matches_collision_and_semantic_color(self):
        for path in (DEFAULT_MAP, DEFAULT_MAP.with_name('short_pit.json')):
            level = load_level(path)
            frame = ColorRenderer().render(level.width, level.height, level.surfaces, level.new_body())
            for row, cells in enumerate(level.terrain):
                for column, tile in enumerate(cells):
                    x, y = column*TILE_SIZE+TILE_SIZE//2, row*TILE_SIZE+TILE_SIZE//2
                    hits = [s for s in level.surfaces
                            if s.x <= x < s.x+s.width and s.y <= y < s.y+s.height]
                    self.assertEqual(bool(hits), tile != '.')
                    if hits:
                        self.assertEqual(hits[0].damage, tile == '^')
                        self.assertEqual(frame.pixels[y*level.width+x], 3 if tile == '^' else 1)

    def test_standard_pit_geometry(self):
        for path in (DEFAULT_MAP, DEFAULT_MAP.with_name('short_pit.json')):
            level = load_level(path)
            surface_row = next(i for i, row in enumerate(level.terrain) if '#' in row)
            hazard_row = next(i for i, row in enumerate(level.terrain) if '^' in row)
            self.assertEqual(level.terrain[surface_row].count('.'), 4)
            self.assertEqual(hazard_row - surface_row, 3)
            self.assertEqual((level.spawn.width, level.spawn.height), (64, 64))

    def test_decorations_have_no_physics_or_sensor_effect(self):
        original = load_level(DEFAULT_MAP)
        data = json.loads(DEFAULT_MAP.read_text())
        data['decorations'] = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'no_decor.json'
            path.write_text(json.dumps(data))
            clean = load_level(path)
        self.assertEqual(original.surfaces, clean.surfaces)
        renderer = ColorRenderer()
        self.assertEqual(renderer.render(original.width, original.height, original.surfaces, original.new_body()),
                         renderer.render(clean.width, clean.height, clean.surfaces, clean.new_body()))

    def test_bad_tile_grid_rejected(self):
        data = json.loads(DEFAULT_MAP.read_text())
        for field, value in [('tile_size', 32), ('terrain', ['?']*12),
                             ('terrain', ['.'*20]*11),
                             ('decorations', [{'sprite':'unknown','column':0,'baseline':7}])]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'bad.json'
                path.write_text(json.dumps(dict(data, **{field: value})))
                with self.assertRaises(ValueError):
                    load_level(path)


if __name__ == '__main__':
    unittest.main()
