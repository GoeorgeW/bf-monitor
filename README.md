# 🔥 BF Monitor (versión sin servidor)

Vigila precios de Black Friday / Cyber Monday y te avisa en el iPhone.
Todo funciona gratis dentro de GitHub, así que no hay ningún servidor que montar:
- **GitHub Actions** revisa los precios cada 30 minutos (aunque tengas el móvil apagado).
- **GitHub Pages** aloja la app.
- **ntfy** te manda la notificación al iPhone.

## Lo que tienes que hacer tú (desde el iPhone, ~5 min)
1. Crea una cuenta gratis en **github.com** (Safari → Sign up).
2. Crea un repositorio vacío: botón **+** → *New repository* → nombre `bf-monitor` → **Public** → *Create repository*.
3. Dile a Claude tu **usuario de GitHub**. Si te pide permiso para conectar GitHub, acéptalo.
   Claude subirá los archivos y activará la web por ti.
4. Abre la web que te dé Claude (`https://TUUSUARIO.github.io/bf-monitor/`) en **Safari** →
   botón Compartir ⬆ → **Añadir a pantalla de inicio**.
5. Instala la app **ntfy** (App Store) → **+** → pega el nombre de canal que aparece en *Ajustes* dentro de BF Monitor.

## Uso diario
- **Añadir producto:** pestaña **+** → pega la URL, nombre, talla y precio objetivo → *Continuar en GitHub* → botón verde **Create**. Aparecerá en 1-2 min.
- **Editar o borrar:** botón **⋯** de la tarjeta → confirma en GitHub con el botón verde.
- **Forzar revisión:** Ajustes → *Forzar revisión ahora* → **Run workflow**.

## Etiquetas
- 🟢 **CHOLLO REAL**: precio ≤ tu objetivo, o nuevo mínimo histórico.
- 🟠 **BAJADA LEVE**: por debajo de la media, pero aún lejos del objetivo.
- 🔴 **PRECIO INFLADO**: ha subido respecto a los últimos días (típico antes de una "falsa oferta").

## A tener en cuenta
- Tu lista de productos es pública (el repositorio es público para que la web sea gratis), igual que el nombre del canal de ntfy, así que alguien que lo encuentre podría leer tus avisos. Si te importa, crea el *secret* `NTFY_TOPIC` con otro nombre (Settings → Secrets → Actions).
- Algunas tiendas (Amazon, Zalando, Nike) bloquean a los robots: la tarjeta lo indicará con un aviso rojo. Idealo, Footshop y muchas otras suelen funcionar.
- GitHub puede retrasar la revisión automática unos minutos en horas punta.
