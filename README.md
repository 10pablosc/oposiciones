# Convocatorias de empleo público con plazas en la provincia de Sevilla

Listado diario de convocatorias de empleo público **en plazo o pendientes de abrir plazo** con posibles plazas en la provincia de Sevilla.

- **Web:** https://10pablosc.github.io/oposiciones/
- **Fuentes:** BOE (sección II.B), BOJA (sección 2.2) y BOP de Sevilla.
- **Actualización:** automática todos los días (GitHub Actions, `.github/workflows/actualizar.yml`). También se puede lanzar a mano desde la pestaña *Actions* → *Actualizar convocatorias* → *Run workflow*.

## Qué incluye
- Ayuntamientos, Diputación, mancomunidades y consorcios de la provincia (bases en el BOP y extracto en el BOE).
- Junta de Andalucía, SAS y entes instrumentales (BOJA).
- Universidad de Sevilla, Pablo de Olavide y UNIA.
- Convocatorias del Estado de ámbito nacional (destino posible en Sevilla).

No incluye concursos de provisión de puestos (solo para personal que ya es funcionario) ni empresas públicas que solo publican en su web.

## Avisos
- Las fechas de fin de plazo se calculan a partir del texto oficial descontando fines de semana y festivos nacionales y de Andalucía. En las convocatorias locales se marcan como *estimadas* porque no se tienen en cuenta los festivos locales.
- Los requisitos se copian literalmente de las bases cuando se pueden localizar. **Lo que vale siempre son las bases oficiales enlazadas.**

## Estructura
- `scraper/actualizar.py`: descarga y procesa los boletines.
- `scraper/textos.py`: extracción de plazos, grupo, titulación y requisitos.
- `data/`: datos intermedios (no editar a mano).
- `docs/`: la web (`index.html`) y el listado (`convocatorias.json`).
