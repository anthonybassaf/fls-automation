import math

def calculate_path_length(path):
    return sum(
        math.dist(path[i], path[i + 1])
        for i in range(len(path) - 1)
    )

def euclidean_distance(p1, p2):
    return math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2 + (p1[2] - p2[2])**2)

def euclidean_distance_2d(a, b):
        return math.sqrt((a.x - b.x)**2 + (a.y - b.y)**2)
