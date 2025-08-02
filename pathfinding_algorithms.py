import heapq
import math

def euclidean_distance(p1, p2):
    return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2 + (p1[2] - p2[2]) ** 2) ** 0.5


def compute_turn_penalty(prev, current, neighbor, base_turn_penalty=0.5):
    if not prev:
        return 0  # No previous direction, no penalty
    dir1 = (current[0] - prev[0], current[1] - prev[1])
    dir2 = (neighbor[0] - current[0], neighbor[1] - current[1])
    if dir1 == dir2:
        return 0  # Continuing in the same direction
    elif dir1[0] * dir2[0] < 0 or dir1[1] * dir2[1] < 0:
        return base_turn_penalty * 2  # Sharp reversal
    else:
        return base_turn_penalty  # Normal turn


def is_jump_allowed(graph, p1, p2):
    """
    Check if all intermediate nodes between p1 and p2 on the line of sight are in the graph and connected.
    """
    x1, y1, z1 = p1
    x2, y2, z2 = p2
    steps = int(euclidean_distance(p1, p2) / 0.2)  # finer sampling
    for i in range(1, steps):
        t = i / steps
        xi = round(x1 + t * (x2 - x1), 4)
        yi = round(y1 + t * (y2 - y1), 4)
        zi = round(z1 + t * (z2 - z1), 4)
        pt = (xi, yi, zi)
        if pt not in graph.nodes:
            return False
        if not graph.has_edge(p1, pt) and not graph.has_edge(pt, p2):
            return False
    return True


def a_star(graph, start, goal, diagonal_penalty_factor=1.05, turn_penalty=0.4):
    open_set = []
    heapq.heappush(open_set, (0, start))

    came_from = {}
    g_score = {node: float('inf') for node in graph.nodes}
    g_score[start] = 0

    f_score = {node: float('inf') for node in graph.nodes}
    f_score[start] = euclidean_distance(start, goal)

    while open_set:
        _, current = heapq.heappop(open_set)

        if current == goal:
            path = []
            while current in came_from:
                path.insert(0, current)
                current = came_from[current]
            path.insert(0, start)
            return path

        for neighbor in graph.neighbors(current):
            weight = graph.edges[current, neighbor]['weight']

            # Diagonal penalty
            dx = abs(current[0] - neighbor[0])
            dy = abs(current[1] - neighbor[1])
            is_diagonal = dx > 0 and dy > 0
            if is_diagonal:
                weight *= diagonal_penalty_factor

            # Turn penalty
            prev = came_from.get(current)
            weight += compute_turn_penalty(prev, current, neighbor, turn_penalty)

            tentative_g_score = g_score[current] + weight
            if tentative_g_score < g_score[neighbor]:
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g_score
                f_score[neighbor] = tentative_g_score + euclidean_distance(neighbor, goal)
                heapq.heappush(open_set, (f_score[neighbor], neighbor))

    return None  # No path found

# def theta_star(graph, start, goal, blockers=None, max_jump_distance=2.0):
    

#     def euclidean_distance(p1, p2):
#         return math.sqrt(sum([(p1[i] - p2[i])**2 for i in range(3)]))

#     def do_segments_intersect(p1, p2, q1, q2):
#         def ccw(a, b, c):
#             return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])
#         return (ccw(p1, q1, q2) != ccw(p2, q1, q2)) and (ccw(p1, p2, q1) != ccw(p1, p2, q2))

#     def line_of_sight(p1, p2, blockers, max_dist=None):
#         if max_dist and euclidean_distance(p1, p2) > max_dist:
#             return False
#         for seg_start, seg_end in blockers or []:
#             if do_segments_intersect((p1[0], p1[1]), (p2[0], p2[1]), seg_start, seg_end):
#                 return False
#         return True
    
#     restricted_rooms = {"WASTE", "STORAGE", "KITCHEN", "MECHANICAL", "ELECTRICAL", "CHEMICAL"}

#     def is_restricted(name):
#         return any(keyword in name for keyword in restricted_rooms)

#     open_set = []
#     heapq.heappush(open_set, (0, start))

#     came_from = {start: None}
#     g_score = {node: float('inf') for node in graph.nodes}
#     g_score[start] = 0

#     f_score = {node: float('inf') for node in graph.nodes}
#     f_score[start] = euclidean_distance(start, goal)

#     while open_set:
#         _, current = heapq.heappop(open_set)

#         if current == goal:
#             path = []
#             while current:
#                 path.insert(0, current)
#                 current = came_from[current]
#             return path
        
#         current_data = graph.nodes[current]
#         current_room_name = current_data.get('room_name', '').upper().strip()

#         for neighbor in graph.neighbors(current):
#             neighbor_data = graph.nodes[neighbor]
#             neighbor_room_name = neighbor_data.get('room_name', '').upper().strip()

#             if is_restricted(neighbor_room_name) and neighbor_room_name != current_room_name:
#                 continue

#             parent = came_from.get(current)

#             if parent and line_of_sight(parent, neighbor, blockers, max_jump_distance):
#                 tentative_g = g_score[parent] + euclidean_distance(parent, neighbor)
#                 if tentative_g < g_score[neighbor]:
#                     came_from[neighbor] = parent
#                     g_score[neighbor] = tentative_g
#                     f_score[neighbor] = tentative_g + euclidean_distance(neighbor, goal)
#                     heapq.heappush(open_set, (f_score[neighbor], neighbor))
#             else:
#                 weight = graph.edges[current, neighbor]['weight']
#                 tentative_g = g_score[current] + weight
#                 if tentative_g < g_score[neighbor]:
#                     came_from[neighbor] = current
#                     g_score[neighbor] = tentative_g
#                     f_score[neighbor] = tentative_g + euclidean_distance(neighbor, goal)
#                     heapq.heappush(open_set, (f_score[neighbor], neighbor))

#     return None

def theta_star(graph, start, goal, blockers=None, furniture=None, max_jump_distance=2.0):
    import math
    import heapq

    def euclidean_distance(p1, p2):
        return math.sqrt(sum([(p1[i] - p2[i]) ** 2 for i in range(3)]))

    def do_segments_intersect(p1, p2, q1, q2):
        def ccw(a, b, c):
            return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])
        return (ccw(p1, q1, q2) != ccw(p2, q1, q2)) and (ccw(p1, p2, q1) != ccw(p1, p2, q2))

    def line_of_sight(p1, p2, blockers, max_dist=None):
        if max_dist and euclidean_distance(p1, p2) > max_dist:
            return False
        for seg_start, seg_end in blockers or []:
            if do_segments_intersect((p1[0], p1[1]), (p2[0], p2[1]), seg_start, seg_end):
                return False
        return True

    # 🔲 Convert furniture to blocker segments
    furniture_blockers = []
    if furniture:
        for obj in furniture:
            if hasattr(obj, "bbox") and obj.bbox:
                try:
                    bb = obj.bbox  # Expected to be an object with xSize, ySize, zSize and a transform
                    tx = obj.transform.matrix[3] / 1000.0
                    ty = obj.transform.matrix[7] / 1000.0
                    w = bb.xSize / 2000.0
                    h = bb.ySize / 2000.0
                    x1, x2 = tx - w, tx + w
                    y1, y2 = ty - h, ty + h
                    furniture_blockers += [
                        ((x1, y1), (x2, y1)),
                        ((x2, y1), (x2, y2)),
                        ((x2, y2), (x1, y2)),
                        ((x1, y2), (x1, y1)),
                    ]
                except Exception as e:
                    print(f"⚠️ Skipping furniture due to error: {e}")

    combined_blockers = (blockers or []) + furniture_blockers

    # ⛔ Optional: avoid crossing into restricted rooms (e.g. shafts)
    restricted_rooms = {"WASTE", "STORAGE", "KITCHEN", "MECHANICAL", "ELECTRICAL", "CHEMICAL"}

    def is_restricted(name):
        return any(keyword in name for keyword in restricted_rooms)

    open_set = []
    heapq.heappush(open_set, (0, start))

    came_from = {start: None}
    g_score = {node: float('inf') for node in graph.nodes}
    g_score[start] = 0

    f_score = {node: float('inf') for node in graph.nodes}
    f_score[start] = euclidean_distance(start, goal)

    while open_set:
        _, current = heapq.heappop(open_set)
        if current == goal:
            path = []
            while current:
                path.insert(0, current)
                current = came_from[current]
            return path

        current_data = graph.nodes[current]
        current_room_name = current_data.get('room_name', '').upper().strip()

        for neighbor in graph.neighbors(current):
            neighbor_data = graph.nodes[neighbor]
            neighbor_room_name = neighbor_data.get('room_name', '').upper().strip()

            if is_restricted(neighbor_room_name) and neighbor_room_name != current_room_name:
                continue

            parent = came_from.get(current)
            if parent and line_of_sight(parent, neighbor, combined_blockers, max_jump_distance):
                tentative_g = g_score[parent] + euclidean_distance(parent, neighbor)
                if tentative_g < g_score[neighbor]:
                    came_from[neighbor] = parent
                    g_score[neighbor] = tentative_g
                    f_score[neighbor] = tentative_g + euclidean_distance(neighbor, goal)
                    heapq.heappush(open_set, (f_score[neighbor], neighbor))
            else:
                weight = graph.edges[current, neighbor]['weight']
                tentative_g = g_score[current] + weight
                if tentative_g < g_score[neighbor]:
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g
                    f_score[neighbor] = tentative_g + euclidean_distance(neighbor, goal)
                    heapq.heappush(open_set, (f_score[neighbor], neighbor))

    return None  # No path found
