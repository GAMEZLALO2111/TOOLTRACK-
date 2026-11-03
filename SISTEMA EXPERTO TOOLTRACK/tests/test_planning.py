import unittest

from planning import calcular_planeacion


class PlanningTests(unittest.TestCase):
    def test_planeacion_calcula_cycle_uph_y_lead(self):
        contenido = "sym = 42\nRUN TIME Sheet : 546 secs = 9.1 min"
        plan = calcular_planeacion(contenido)
        self.assertEqual(plan["piezas_blank"], 42)
        self.assertEqual(plan["cycle_time_s"], 546)
        self.assertEqual(plan["cycle_con_lead_s"], 726)
        self.assertEqual(plan["uph_teorico"], 276.9)

    def test_planeacion_sin_datos_regresa_none(self):
        self.assertIsNone(calcular_planeacion("sin nada util"))


if __name__ == "__main__":
    unittest.main()
