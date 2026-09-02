# ¿Está caída la sede? — estado-sede.org

**[estado-sede.org](https://estado-sede.org)** comprueba con peticiones reales
los enlaces oficiales de la administración española que más se buscan — sedes
electrónicas, trámites y entidades de ayuda — y te dice si responden.

Monitor independiente. No afiliado a ninguna administración.

## Cómo funciona

- `check.py` visita cada enlace y lo clasifica. No se fía del código HTTP:
  detecta **soft 404** (un «página no encontrada» servido con 200, o un enlace
  profundo que colapsa a la portada), distingue **bloqueo de robots** de caída
  real (`No verificable`, nunca «caída»), y solo señala un problema tras
  **3 comprobaciones fallidas consecutivas**.
- Los 8 servicios más frágiles (cita de extranjería, DNI, Cl@ve…) se comprueban
  **cada hora**; los 63 del listado, a diario — dentro de la ventana horaria
  que pide el `robots.txt` de cada sitio, con un User-Agent identificado y
  contacto real.
- `build_site.py` genera una página estática por servicio con el estado, el
  historial y adónde acudir si no funciona. GitHub Actions lo ejecuta todo y
  publica en GitHub Pages.
- `overrides.json` recoge observaciones manuales con caducidad, para cuando un
  cortafuegos le esconde al robot lo que una persona sí puede ver.

## Lo que este monitor no hace

Ni consejos sobre trámites, ni cuentas, ni rastreo, ni datos personales, ni
citas previas automatizadas. Solo hechos con fecha y hora: si el enlace oficial
respondió, y adónde ir.

## Ejecutar en local

```bash
pip install -r requirements.txt
python check.py            # comprueba todo
python check.py --watch    # solo el nivel horario
python build_site.py       # genera ./site/
```

Los datos viven en `seeds.json` (fuentes, descripciones y etiquetas) y en
`checks.db` / `status.json` (resultados, versionados por el bot en cada run).

¿Falta un enlace oficial que la gente busca? Abre un issue.
