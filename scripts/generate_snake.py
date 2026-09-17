import json
import os
import random
import sys
import urllib.request
from datetime import datetime, timezone
from html import escape

GRID_ROWS = 7

# Aparência
CELL = 10
GAP = 3
PITCH = CELL + GAP

# Velocidade: tempo aproximado de cada movimento da cobrinha.
# Quanto MAIOR, mais devagar.
STEP_SECONDS = 0.10
MIN_DURATION_SECONDS = 18

# Crescimento
START_LENGTH = 3
GROW_EVERY = 1
ABSOLUTE_MAX_LENGTH = 30

PALETTES = {
    "dark": {
        "background": "transparent",
        "empty": "#161b22",
        "levels": {
            "FIRST_QUARTILE": "#0e4429",
            "SECOND_QUARTILE": "#006d32",
            "THIRD_QUARTILE": "#26a641",
            "FOURTH_QUARTILE": "#39d353",
        },
        "snake": "#58a6ff",
        "head": "#79c0ff",
    },
    "light": {
        "background": "transparent",
        "empty": "#ebedf0",
        "levels": {
            "FIRST_QUARTILE": "#9be9a8",
            "SECOND_QUARTILE": "#40c463",
            "THIRD_QUARTILE": "#30a14e",
            "FOURTH_QUARTILE": "#216e39",
        },
        "snake": "#0969da",
        "head": "#218bff",
    },
}

QUERY = r"""query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        weeks {
          contributionDays {
            date
            contributionCount
            contributionLevel
          }
        }
      }
    }
  }
}"""


def fetch_calendar(username: str, token: str):
    payload = json.dumps(
        {"query": QUERY, "variables": {"login": username}}
    ).encode()

    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "growing-contribution-snake",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode())

    if data.get("errors"):
        raise RuntimeError(data["errors"])

    user = data.get("data", {}).get("user")

    if not user:
        raise RuntimeError(
            f"Usuário '{username}' não encontrado no GitHub."
        )

    return user["contributionsCollection"]["contributionCalendar"]["weeks"]


def build_grid(weeks):
    cols = len(weeks)

    grid = [
        [
            {"count": 0, "level": "NONE", "date": ""}
            for _ in range(GRID_ROWS)
        ]
        for _ in range(cols)
    ]

    for x, week in enumerate(weeks):
        for day in week["contributionDays"]:
            dt = datetime.strptime(day["date"], "%Y-%m-%d")

            # Python: segunda=0
            # GitHub: domingo no topo
            y = (dt.weekday() + 1) % 7

            grid[x][y] = {
                "count": int(day["contributionCount"]),
                "level": day["contributionLevel"],
                "date": day["date"],
            }

    return grid


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def random_shortest_route(start, target):
    """
    Cria um caminho contínuo entre dois pontos.
    Ele continua sendo um caminho curto, mas alterna aleatoriamente
    movimentos horizontais e verticais para evitar o aspecto de zigue-zague.
    """
    x, y = start
    tx, ty = target
    route = []

    while (x, y) != (tx, ty):
        options = []

        if x < tx:
            options.append((x + 1, y))
        elif x > tx:
            options.append((x - 1, y))

        if y < ty:
            options.append((x, y + 1))
        elif y > ty:
            options.append((x, y - 1))

        x, y = random.choice(options)
        route.append((x, y))

    return route


def create_path(grid):
    cols = len(grid)

    # A rota muda uma vez por dia.
    # Rodar o workflow novamente no mesmo dia mantém a mesma animação.
    seed = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    random.seed(seed)

    active = {
        (x, y)
        for x in range(cols)
        for y in range(GRID_ROWS)
        if grid[x][y]["count"] > 0
    }

    path = []

    # Entrada pela esquerda.
    for x in range(-START_LENGTH, 0):
        path.append((x, 0))

    current = (0, 0)
    path.append(current)
    active.discard(current)

    while active:
        # Evita saltos gigantes, mas não escolhe sempre o quadrado mais próximo.
        ordered = sorted(
            active,
            key=lambda target: manhattan(current, target)
        )

        candidate_count = min(8, len(ordered))
        candidates = ordered[:candidate_count]

        # Dá preferência aos mais próximos sem tornar a rota previsível.
        weights = list(range(candidate_count, 0, -1))
        target = random.choices(
            candidates,
            weights=weights,
            k=1,
        )[0]

        route = random_shortest_route(current, target)

        for step in route:
            path.append(step)
            active.discard(step)

        current = target

    # Faz a cobrinha sair do gráfico para a cauda terminar a animação.
    x, y = current

    if x < cols / 2:
        direction = -1
        steps_to_exit = x + ABSOLUTE_MAX_LENGTH + 3
    else:
        direction = 1
        steps_to_exit = (cols - 1 - x) + ABSOLUTE_MAX_LENGTH + 3

    for _ in range(steps_to_exit):
        x += direction
        path.append((x, y))

    return path


def contribution_color(cell, theme):
    palette = PALETTES[theme]

    if cell["count"] <= 0:
        return palette["empty"]

    return palette["levels"].get(
        cell["level"],
        palette["levels"]["FIRST_QUARTILE"],
    )


def make_key_times(amount):
    if amount <= 1:
        return "0;1"

    return ";".join(
        f"{i / (amount - 1):.6f}"
        for i in range(amount)
    )


def generate_svg(grid, theme):
    cols = len(grid)
    palette = PALETTES[theme]

    active_cells = sum(
        1
        for col in grid
        for cell in col
        if cell["count"] > 0
    )

    # Nunca passa de 30 e nunca fica maior que a quantidade
    # de quadrados que possuem contribuição.
    max_length = max(
        1,
        min(ABSOLUTE_MAX_LENGTH, active_cells),
    )

    initial_length = min(
        START_LENGTH,
        max_length,
    )

    path = create_path(grid)
    frame_count = len(path)

    # Velocidade constante por movimento.
    duration_seconds = max(
        MIN_DURATION_SECONDS,
        frame_count * STEP_SECONDS,
    )

    cell_path_index = {}

    for index, (x, y) in enumerate(path):
        if (
            0 <= x < cols
            and 0 <= y < GRID_ROWS
            and (x, y) not in cell_path_index
        ):
            cell_path_index[(x, y)] = index

    eaten_positions = set()
    length_by_frame = []

    for x, y in path:
        if (
            0 <= x < cols
            and 0 <= y < GRID_ROWS
            and grid[x][y]["count"] > 0
        ):
            eaten_positions.add((x, y))

        eaten = len(eaten_positions)

        current_length = min(
            max_length,
            initial_length + eaten // GROW_EVERY,
        )

        length_by_frame.append(
            max(1, current_length)
        )

    width = cols * PITCH - GAP
    height = GRID_ROWS * PITCH - GAP
    key_times = make_key_times(frame_count)

    svg = []

    svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" '
        f'role="img" aria-label="GitHub contribution snake">'
    )

    if palette["background"] != "transparent":
        svg.append(
            f'<rect width="100%" height="100%" '
            f'fill="{palette["background"]}"/>'
        )

    # =========================
    # QUADRADOS DE CONTRIBUIÇÃO
    # =========================
    svg.append('<g id="contributions">')

    for x in range(cols):
        for y in range(GRID_ROWS):
            cell = grid[x][y]

            px = x * PITCH
            py = y * PITCH

            color = contribution_color(
                cell,
                theme,
            )

            date_text = cell["date"] or "sem data"

            title = escape(
                f'{date_text}: '
                f'{cell["count"]} contribuições'
            )

            svg.append(
                f'<rect x="{px}" y="{py}" '
                f'width="{CELL}" height="{CELL}" '
                f'rx="2" fill="{color}">'
            )

            svg.append(
                f"<title>{title}</title>"
            )

            if cell["count"] > 0:
                eat_index = cell_path_index[(x, y)]

                eat_time = eat_index / max(
                    1,
                    frame_count - 1,
                )

                epsilon = min(
                    0.0015,
                    1 / max(
                        1000,
                        frame_count * 10,
                    ),
                )

                after_eat = min(
                    1,
                    eat_time + epsilon,
                )

                svg.append(
                    f'<animate '
                    f'attributeName="opacity" '
                    f'dur="{duration_seconds:.2f}s" '
                    f'repeatCount="indefinite" '
                    f'calcMode="discrete" '
                    f'values="1;1;0;0" '
                    f'keyTimes="0;'
                    f'{eat_time:.6f};'
                    f'{after_eat:.6f};1"/>'
                )

            svg.append("</rect>")

    svg.append("</g>")

    # =========================
    # COBRINHA
    # =========================
    svg.append('<g id="snake">')

    for segment in range(max_length):
        x_values = []
        y_values = []
        opacity_values = []

        for frame in range(frame_count):
            visible = (
                segment < length_by_frame[frame]
                and frame - segment >= 0
            )

            if visible:
                x, y = path[frame - segment]

                x_values.append(
                    str(x * PITCH)
                )

                y_values.append(
                    str(y * PITCH)
                )

                opacity_values.append("1")

            else:
                x_values.append(
                    str(-PITCH * 4)
                )

                y_values.append("0")
                opacity_values.append("0")

        fill = (
            palette["head"]
            if segment == 0
            else palette["snake"]
        )

        radius = (
            3
            if segment == 0
            else 2
        )

        svg.append(
            f'<rect x="{-PITCH * 4}" y="0" '
            f'width="{CELL}" height="{CELL}" '
            f'rx="{radius}" fill="{fill}">'
        )

        svg.append(
            f'<animate attributeName="x" '
            f'dur="{duration_seconds:.2f}s" '
            f'repeatCount="indefinite" '
            f'values="{";".join(x_values)}" '
            f'keyTimes="{key_times}"/>'
        )

        svg.append(
            f'<animate attributeName="y" '
            f'dur="{duration_seconds:.2f}s" '
            f'repeatCount="indefinite" '
            f'values="{";".join(y_values)}" '
            f'keyTimes="{key_times}"/>'
        )

        svg.append(
            f'<animate attributeName="opacity" '
            f'dur="{duration_seconds:.2f}s" '
            f'repeatCount="indefinite" '
            f'calcMode="discrete" '
            f'values="{";".join(opacity_values)}" '
            f'keyTimes="{key_times}"/>'
        )

        svg.append("</rect>")

    svg.append("</g>")

    svg.append(
        f'<!-- '
        f'active_cells={active_cells}; '
        f'max_snake_length={max_length}; '
        f'grow_every={GROW_EVERY}; '
        f'frames={frame_count}; '
        f'duration={duration_seconds:.2f}s '
        f'-->'
    )

    svg.append("</svg>")

    return "\n".join(svg)


def main():
    username = (
        os.environ.get("GITHUB_USER")
        or (
            sys.argv[1]
            if len(sys.argv) > 1
            else ""
        )
    )

    token = (
        os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
    )

    if not username:
        raise SystemExit(
            "Defina GITHUB_USER ou passe "
            "o usuário como primeiro argumento."
        )

    if not token:
        raise SystemExit(
            "Defina GH_TOKEN ou GITHUB_TOKEN."
        )

    weeks = fetch_calendar(
        username,
        token,
    )

    grid = build_grid(weeks)

    os.makedirs(
        "dist",
        exist_ok=True,
    )

    files = {
        "light":
            "github-contribution-grid-snake.svg",
        "dark":
            "github-contribution-grid-snake-dark.svg",
    }

    for theme, filename in files.items():
        output_path = os.path.join(
            "dist",
            filename,
        )

        with open(
            output_path,
            "w",
            encoding="utf-8",
        ) as file:
            file.write(
                generate_svg(
                    grid,
                    theme,
                )
            )

        print(
            f"Gerado: {output_path}"
        )


if __name__ == "__main__":
    main()
