# -*- coding: utf-8 -*-
"""
ダイヤモンドゲーム（Chinese Checkers）デスクトップ版
  - 盤面: diamond_board.svg 準拠（121ノード / 中央六角形 R=4 + 三角キャンプ L=4 ×6）
  - ルール: Spec v1.2 / 15駒ルール（キャンプ10 + 基底辺5）
  - AI: 初級 = 貪欲法 / 上級 = ミニマックス + アルファベータ枝刈り
  - 5秒無操作で自動操作へ移行、自動操作中のクリックで以後ずっと手動操作
"""

import math
import random
import sys
import time

from PyQt5.QtCore import QPointF, QRectF, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QPolygonF,
    QRadialGradient,
)
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

# =============================================================================
# 1. 盤面ジオメトリ（Axial座標 / Cube座標: x = q, z = r, y = -q - r）
# =============================================================================

HEX_R = 4  # 中央正六角形の半径
CAMP_L = 4  # 三角キャンプの辺長
SQRT3_2 = math.sqrt(3) / 2.0

AXES = ("x", "y", "z")
AXIS_INDEX = {"x": 0, "y": 1, "z": 2}

# 隣接6方向（Axial）
DIRECTIONS = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]
# グリッド線を引く3方向
EDGE_DIRECTIONS = [(1, 0), (0, 1), (1, -1)]

# 6つの三角キャンプ（diamond_board.svg のカラーパレット）
CAMP_KEYS = [("x", +1), ("z", -1), ("y", +1), ("x", -1), ("z", +1), ("y", -1)]
CAMP_COLORS = {
    ("x", +1): "#7dd3fc",  # 水色
    ("z", -1): "#d946ef",  # 紫
    ("y", +1): "#22c55e",  # 緑（P1初期陣地）
    ("x", -1): "#ef4444",  # 赤
    ("z", +1): "#f59e0b",  # 橙
    ("y", -1): "#fde047",  # 黄（P2初期陣地）
}
CAMP_NAMES = {
    ("x", +1): "水色",
    ("z", -1): "紫",
    ("y", +1): "緑",
    ("x", -1): "赤",
    ("z", +1): "橙",
    ("y", -1): "黄",
}

COLOR_BG = "#d9bea2"
COLOR_HEX = "#ffffff"
COLOR_EDGE = "#333333"
COLOR_NODE = "#4b2e1f"
COLOR_TEXT_DARK = "#2e1a0f"
COLOR_TEXT_SUB = "#5c3a21"

PIECE_COLORS = ["#1d4ed8", "#b91c1c", "#16a34a"]  # P1 青 / P2 赤 / P3 緑
PIECE_COLOR_NAMES = ["青", "赤", "緑"]

# 人数ごとの初期陣地（P1, P2, P3 の順）。ゴールは向かい側（同じ軸・逆符号）。
PLAYER_CAMPS = {
    2: [("y", +1), ("y", -1)],  # 緑 vs 黄（対向）
    3: [("y", +1), ("x", +1), ("z", +1)],  # 緑 / 水色 / 橙（120°間隔）
}


def cube_of(q, r):
    return (q, -q - r, r)


def axial_of_cube(x, y, z):
    return (x, z)


def hex_distance(a, b):
    return (abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])) / 2.0


def in_hexagon(q, r):
    return max(abs(q), abs(r), abs(-q - r)) <= HEX_R


def in_camp(q, r, key):
    axis, sign = key
    c = cube_of(q, r)
    ai = AXIS_INDEX[axis]
    if sign * c[ai] <= HEX_R:
        return False
    for i in range(3):
        if i != ai and sign * c[i] < -HEX_R:
            return False
    return True


def camp_of(q, r):
    for key in CAMP_KEYS:
        if in_camp(q, r, key):
            return key
    return None


def opposite(key):
    return (key[0], -key[1])


class Geometry:
    """121ノードの盤面と、隣接・ジャンプ表、各種領域を前計算する。"""

    def __init__(self):
        self.nodes = []
        for q in range(-8, 9):
            for r in range(-8, 9):
                if in_hexagon(q, r) or camp_of(q, r) is not None:
                    self.nodes.append((q, r))
        self.index = {n: i for i, n in enumerate(self.nodes)}
        self.n = len(self.nodes)
        assert self.n == 121, self.n
        self.cube = [cube_of(q, r) for (q, r) in self.nodes]
        self.camp = [camp_of(q, r) for (q, r) in self.nodes]

        # 隣接表とジャンプ表（over: 飛び越える駒, land: 着地点）
        self.neigh = [[] for _ in range(self.n)]
        self.jumps = [[] for _ in range(self.n)]
        for i, (q, r) in enumerate(self.nodes):
            for dq, dr in DIRECTIONS:
                nb = self.index.get((q + dq, r + dr))
                if nb is None:
                    continue
                self.neigh[i].append(nb)
                land = self.index.get((q + 2 * dq, r + 2 * dr))
                if land is not None:
                    self.jumps[i].append((nb, land))

        # グリッド線（3方向）
        self.edges = []
        for i, (q, r) in enumerate(self.nodes):
            for dq, dr in EDGE_DIRECTIONS:
                j = self.index.get((q + dq, r + dr))
                if j is not None:
                    self.edges.append((i, j))

        # 各キャンプ（10ノード）と基底辺（中央六角形側の5ノード）
        self.camp_nodes = {}
        self.base_edge = {}
        for key in CAMP_KEYS:
            axis, sign = key
            ai = AXIS_INDEX[axis]
            self.camp_nodes[key] = [i for i in range(self.n) if self.camp[i] == key]
            self.base_edge[key] = [
                i
                for i in range(self.n)
                if self.camp[i] is None and sign * self.cube[i][ai] == HEX_R
            ]
            assert len(self.camp_nodes[key]) == 10
            assert len(self.base_edge[key]) == 5

    def region15(self, key):
        """キャンプ10 + 基底辺5 = 15マス"""
        return self.camp_nodes[key] + self.base_edge[key]

    @staticmethod
    def to_xy(q, r, scale):
        return ((q + r / 2.0) * scale, SQRT3_2 * r * scale)

    @staticmethod
    def hexagon_corners():
        return [(4, 0), (4, -4), (0, -4), (-4, 0), (-4, 4), (0, 4)]

    @staticmethod
    def camp_triangle(key):
        """三角キャンプの頂点（基底辺の両端 + 先端）を Axial 座標で返す。"""
        axis, sign = key
        ai = AXIS_INDEX[axis]
        others = [i for i in range(3) if i != ai]
        pts = []
        for a, b in ((0, -4), (-4, 0)):
            c = [0, 0, 0]
            c[ai] = 4 * sign
            c[others[0]] = a * sign
            c[others[1]] = b * sign
            pts.append(axial_of_cube(*c))
        c = [0, 0, 0]
        c[ai] = 8 * sign
        c[others[0]] = -4 * sign
        c[others[1]] = -4 * sign
        pts.append(axial_of_cube(*c))
        return pts


GEO = Geometry()


# =============================================================================
# 2. ゲーム状態とルール
# =============================================================================


class GameConfig:
    def __init__(self, num_players=2, kinds=None, difficulty="hard", forbid_goal_exit=True, anti_spoil=False):
        self.num_players = num_players
        self.kinds = kinds or ["human", "ai"]  # "human" / "ai"
        self.difficulty = difficulty  # "easy" / "hard"
        self.forbid_goal_exit = forbid_goal_exit
        # 妨害防止（任意・既定OFF）: ゴールが埋まり、空きが相手駒だけなら勝利とみなす
        self.anti_spoil = anti_spoil


class PlayerInfo:
    def __init__(self, idx, start_key):
        self.idx = idx
        self.start_key = start_key
        self.goal_key = opposite(start_key)
        self.start = GEO.region15(start_key)
        self.start_set = frozenset(self.start)
        self.goal = frozenset(GEO.region15(self.goal_key))
        goal_axis, goal_sign = self.goal_key
        self.goal_axis = AXIS_INDEX[goal_axis]
        self.goal_sign = goal_sign
        cs = [GEO.cube[i] for i in self.goal]
        self.centroid = tuple(sum(c[k] for c in cs) / len(cs) for k in range(3))
        # 各ノードの「ゴールへの近さ」評価値（前計算）
        self.progress = [goal_sign * GEO.cube[i][self.goal_axis] for i in range(GEO.n)]
        self.dist = [hex_distance(GEO.cube[i], self.centroid) for i in range(GEO.n)]
        self.value = []
        for i in range(GEO.n):
            if i in self.goal:
                v = 14.0 + 1.0 * self.progress[i]
            else:
                v = 1.0 * self.progress[i] - 1.3 * self.dist[i]
            self.value.append(v)


class GameState:
    def __init__(self, config):
        self.config = config
        self.players = [PlayerInfo(i, k) for i, k in enumerate(PLAYER_CAMPS[config.num_players])]
        self.board = [-1] * GEO.n  # 各ノードの所有プレイヤー（-1 = 空）
        self.labels = [0] * GEO.n  # 駒番号（1〜15）
        self.pos = [set() for _ in self.players]
        for p in self.players:
            # 先端に近い順に 1〜15 の番号を振る
            order = sorted(p.start, key=lambda i: p.progress[i])
            for num, node in enumerate(order, start=1):
                self.board[node] = p.idx
                self.labels[node] = num
                self.pos[p.idx].add(node)
        self.current = 0
        self.ply = 0
        self.history = []  # (player, from, to, path)
        self.winner = None
        self.best_in_goal = [0] * len(self.players)  # 膠着判定用
        self.last_progress_ply = 0

    def apply(self, player, f, t, path):
        self.board[f] = -1
        self.board[t] = player
        self.labels[t] = self.labels[f]
        self.labels[f] = 0
        self.pos[player].discard(f)
        self.pos[player].add(t)
        self.history.append((player, f, t, list(path)))
        self.ply += 1
        in_goal = len(self.players[player].goal & self.pos[player])
        if in_goal > self.best_in_goal[player]:
            self.best_in_goal[player] = in_goal
            self.last_progress_ply = self.ply
        if is_winner(self.board, self.pos, self.players, player, self.config.anti_spoil):
            self.winner = player

    def next_player(self):
        self.current = (self.current + 1) % len(self.players)

    def last_move_of(self, player):
        for h in reversed(self.history):
            if h[0] == player:
                return h
        return None


def is_winner(board, pos, players, p, anti_spoil=False):
    """
    ゴール15マスすべてを自駒で占有したら勝利。
    anti_spoil=True の場合は、ゴールに空きがなく自駒が1つ以上あれば
    （残りが相手駒による封鎖でも）勝利とみなす。
    """
    goal = players[p].goal
    mine = len(goal & pos[p])
    if mine == len(goal):
        return True
    if anti_spoil and mine > 0:
        for n in goal:
            if board[n] == -1:
                return False
        return True
    return False


def generate_moves(board, pos_p, goal, forbid_exit, with_paths=False):
    """
    合法手を生成する。
    返り値: [(from, to, path or None), ...]
      - ステップ: 隣接する空きノードへ1マス
      - ジャンプ: 隣接駒を挟んだ反対側の空きノードへ。連続ジャンプ可能
      - ゴール退出禁止: ゴール内の駒はゴール外へ出る手を非合法とする
    """
    moves = []
    neigh = GEO.neigh
    jumps = GEO.jumps
    for f in list(pos_p):
        locked = forbid_exit and f in goal
        dests = {}
        for nb in neigh[f]:
            if board[nb] == -1 and (not locked or nb in goal):
                dests[nb] = (f, nb) if with_paths else None
        # 連続ジャンプ（出発点は空いているものとして扱う）
        owner = board[f]
        board[f] = -1
        parent = {f: None}
        stack = [f]
        while stack:
            c = stack.pop()
            for over, land in jumps[c]:
                if board[over] != -1 and board[land] == -1 and land not in parent:
                    parent[land] = c
                    stack.append(land)
        board[f] = owner
        for land in parent:
            if land == f or land in dests:
                continue
            if locked and land not in goal:
                continue
            if with_paths:
                path = []
                c = land
                while c is not None:
                    path.append(c)
                    c = parent[c]
                path.reverse()
                dests[land] = tuple(path)
            else:
                dests[land] = None
        for t, path in dests.items():
            moves.append((f, t, path))
    return moves


# =============================================================================
# 3. AI（初級: 貪欲法 / 上級: ミニマックス + アルファベータ枝刈り）
# =============================================================================


class SearchTimeout(Exception):
    pass


class AIEngine:
    WIN_SCORE = 100000.0

    def __init__(self, players, forbid_exit, difficulty, anti_spoil=False, time_limit=2.0, max_depth=3, beam=12):
        self.players = players
        self.forbid_exit = forbid_exit
        self.anti_spoil = anti_spoil
        # 3人戦では他者2人を同時に悲観視しすぎないよう相手評価の重みを下げる
        self.others_weight = 1.0 if len(players) == 2 else 0.5
        self.difficulty = difficulty
        self.time_limit = time_limit
        self.max_depth = max_depth
        self.beam = beam
        self.deadline = 0.0
        self.nodes = 0

    # ---------------- 評価関数 ----------------
    def player_score(self, board, pos, i):
        p = self.players[i]
        val = p.value
        s = 0.0
        worst = 0.0
        in_goal = 0
        for n in pos[i]:
            s += val[n]
            if n in p.goal:
                in_goal += 1
            else:
                d = p.dist[n]
                if d > worst:
                    worst = d
        # 取り残された駒・自陣に残った駒へのペナルティ
        s -= 1.5 * worst
        s -= 2.0 * len(pos[i] & p.start_set)
        # ゴール到達駒の重み付け
        s += 3.0 * in_goal
        # ジャンプ可能手数のボーナス（前進するジャンプのみ）
        jumps = 0
        for n in pos[i]:
            vn = val[n]
            for over, land in GEO.jumps[n]:
                if board[over] != -1 and board[land] == -1 and val[land] > vn:
                    jumps += 1
        s += 0.3 * min(jumps, 15)
        return s

    def evaluate(self, board, pos, me):
        mine = self.player_score(board, pos, me)
        others = [self.player_score(board, pos, i) for i in range(len(self.players)) if i != me]
        return mine - self.others_weight * sum(others) / len(others)

    # ---------------- 指し手選択 ----------------
    def choose(self, board, pos, me, last_move=None):
        board = list(board)
        pos = [set(s) for s in pos]
        moves = generate_moves(board, pos[me], self.players[me].goal, self.forbid_exit, with_paths=True)
        if not moves:
            return None
        # 即勝ちの手があれば最優先
        for f, t, path in moves:
            pos[me].discard(f)
            pos[me].add(t)
            board[f] = -1
            board[t] = me
            win = is_winner(board, pos, self.players, me, self.anti_spoil)
            board[t] = -1
            board[f] = me
            pos[me].discard(t)
            pos[me].add(f)
            if win:
                return (f, t, path)
        if self.difficulty == "easy":
            return self.choose_greedy(moves, me, last_move)
        return self.choose_alphabeta(board, pos, me, moves, last_move)

    def choose_greedy(self, moves, me, last_move):
        """初級: ゴール方向へ最も進む手・最長ジャンプを優先する貪欲法。"""
        p = self.players[me]
        best, best_score = None, -1e18
        for f, t, path in moves:
            gain = p.value[t] - p.value[f]
            hops = len(path) - 1 if path and len(path) > 2 else 0
            # 後方に取り残された駒ほど優先（自陣に閉じ込められるのを防ぐ）
            rear = p.dist[f] if f not in p.goal else 0.0
            score = gain * 10.0 + hops * 1.5 + rear * 0.8 + random.random() * 0.8
            if last_move is not None and last_move[2] == f and last_move[1] == t:
                score -= 30.0  # 直前の手を戻すだけの往復を避ける
            if score > best_score:
                best, best_score = (f, t, path), score
        return best

    def order_key(self, mover, f, t):
        v = self.players[mover].value
        return v[t] - v[f]

    def choose_alphabeta(self, board, pos, me, moves, last_move):
        """上級: 反復深化 + ミニマックス（他プレイヤーは全員最小化側）+ アルファベータ枝刈り。"""
        self.deadline = time.time() + self.time_limit
        self.nodes = 0
        n_players = len(self.players)

        def root_bias(f, t):
            b = random.random() * 0.05
            if last_move is not None and last_move[2] == f and last_move[1] == t:
                b -= 5.0
            return b

        moves.sort(key=lambda m: self.order_key(me, m[0], m[1]), reverse=True)
        bias = {(f, t): root_bias(f, t) for f, t, _ in moves}
        best_move = moves[0]
        for depth in range(1, self.max_depth + 1):
            try:
                alpha = -1e18
                cur_best, cur_val = None, -1e18
                for f, t, path in moves:
                    self._do(board, pos, me, f, t)
                    try:
                        if is_winner(board, pos, self.players, me, self.anti_spoil):
                            v = self.WIN_SCORE + depth
                        else:
                            v = self.alphabeta(board, pos, depth - 1, alpha, 1e18, (me + 1) % n_players, me)
                    finally:
                        self._undo(board, pos, me, f, t)
                    v += bias[(f, t)]
                    if v > cur_val:
                        cur_val, cur_best = v, (f, t, path)
                    if v > alpha:
                        alpha = v
                best_move = cur_best
                # 次の深さでは最善手から探索（枝刈り効率向上）
                moves.remove(cur_best)
                moves.insert(0, cur_best)
            except SearchTimeout:
                break
        return best_move

    def alphabeta(self, board, pos, depth, alpha, beta, mover, me):
        self.nodes += 1
        if (self.nodes & 255) == 0 and time.time() > self.deadline:
            raise SearchTimeout()
        if depth == 0:
            return self.evaluate(board, pos, me)
        n_players = len(self.players)
        moves = generate_moves(board, pos[mover], self.players[mover].goal, self.forbid_exit)
        if not moves:
            return self.alphabeta(board, pos, depth - 1, alpha, beta, (mover + 1) % n_players, me)
        moves.sort(key=lambda m: self.order_key(mover, m[0], m[1]), reverse=True)
        moves = moves[: self.beam]
        maximizing = mover == me
        best = -1e18 if maximizing else 1e18
        for f, t, _ in moves:
            self._do(board, pos, mover, f, t)
            try:
                if is_winner(board, pos, self.players, mover, self.anti_spoil):
                    v = (self.WIN_SCORE + depth) if maximizing else -(self.WIN_SCORE + depth)
                else:
                    v = self.alphabeta(board, pos, depth - 1, alpha, beta, (mover + 1) % n_players, me)
            finally:
                self._undo(board, pos, mover, f, t)
            if maximizing:
                if v > best:
                    best = v
                if best > alpha:
                    alpha = best
            else:
                if v < best:
                    best = v
                if best < beta:
                    beta = best
            if alpha >= beta:
                break
        return best

    @staticmethod
    def _do(board, pos, p, f, t):
        board[f] = -1
        board[t] = p
        pos[p].discard(f)
        pos[p].add(t)

    @staticmethod
    def _undo(board, pos, p, f, t):
        board[t] = -1
        board[f] = p
        pos[p].discard(t)
        pos[p].add(f)


class AIWorker(QThread):
    """UIを止めないよう、AIの思考は別スレッドで実行する。"""

    finished_move = pyqtSignal(int, object)  # token, (from, to, path) or None

    def __init__(self, token, engine, board, pos, me, last_move):
        super().__init__()
        self.token = token
        self.engine = engine
        self.board = list(board)
        self.pos = [set(s) for s in pos]
        self.me = me
        self.last_move = last_move

    def run(self):
        start = time.time()
        move = self.engine.choose(self.board, self.pos, self.me, self.last_move)
        # 一瞬で指すと見づらいので最低限の「考慮時間」を確保
        rest = 0.35 - (time.time() - start)
        if rest > 0:
            time.sleep(rest)
        self.finished_move.emit(self.token, move)


# =============================================================================
# 4. 盤面ウィジェット（描画とクリック）
# =============================================================================


class BoardWidget(QWidget):
    clicked_node = pyqtSignal(int)  # ノード番号（盤外クリックは -1）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(560, 600)
        self.state = None
        self.selected = None
        self.targets = {}  # to -> path
        self.anim = None  # dict(player, label, path, t)
        self.setMouseTracking(True)

    # ----- 座標変換 -----
    def _layout(self):
        w, h = self.width(), self.height()
        top_margin = 64
        bottom_margin = 40
        avail_h = h - top_margin - bottom_margin
        scale = min(w / (2 * 6.0 + 1.6), avail_h / (2 * 8 * SQRT3_2 + 1.6))
        cx = w / 2.0
        cy = top_margin + avail_h / 2.0
        return scale, cx, cy

    def node_xy(self, i, layout=None):
        scale, cx, cy = layout or self._layout()
        q, r = GEO.nodes[i]
        x, y = Geometry.to_xy(q, r, scale)
        return QPointF(cx + x, cy + y)

    def axial_xy(self, q, r, layout):
        scale, cx, cy = layout
        x, y = Geometry.to_xy(q, r, scale)
        return QPointF(cx + x, cy + y)

    def node_at(self, pt):
        layout = self._layout()
        scale = layout[0]
        best, best_d = -1, 0.48 * scale
        for i in range(GEO.n):
            p = self.node_xy(i, layout)
            d = math.hypot(p.x() - pt.x(), p.y() - pt.y())
            if d < best_d:
                best, best_d = i, d
        return best

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked_node.emit(self.node_at(ev.pos()))

    # ----- 描画 -----
    def paintEvent(self, _ev):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        layout = self._layout()
        scale = layout[0]

        # 背景（木製ボード風）
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(COLOR_BG))
        painter.drawRoundedRect(QRectF(self.rect()), 12, 12)

        # タイトル
        painter.setPen(QColor(COLOR_TEXT_DARK))
        f = QFont(self.font())
        f.setPointSizeF(13)
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(QRectF(0, 6, self.width(), 26), Qt.AlignCenter, "ダイヤモンドゲーム (121ノード / R=L=4)")
        f.setPointSizeF(9)
        f.setBold(False)
        painter.setFont(f)
        painter.setPen(QColor(COLOR_TEXT_SUB))
        painter.drawText(
            QRectF(0, 32, self.width(), 20), Qt.AlignCenter, "Spec v1.2：各プレイヤー15駒（キャンプ10 + 基底辺5）"
        )

        # 中央正六角形（白・角丸）と6つの三角キャンプ
        round_pen = QPen(QColor("#ffffff"), max(3.0, scale * 0.28))
        round_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(round_pen)
        painter.setBrush(QColor(COLOR_HEX))
        painter.drawPolygon(QPolygonF([self.axial_xy(q, r, layout) for q, r in Geometry.hexagon_corners()]))
        for key in CAMP_KEYS:
            painter.setBrush(QColor(CAMP_COLORS[key]))
            painter.drawPolygon(QPolygonF([self.axial_xy(q, r, layout) for q, r in Geometry.camp_triangle(key)]))

        # エッジ（三角格子）
        edge_color = QColor(COLOR_EDGE)
        edge_color.setAlphaF(0.6)
        painter.setPen(QPen(edge_color, 1.0))
        for i, j in GEO.edges:
            painter.drawLine(self.node_xy(i, layout), self.node_xy(j, layout))

        # 直前の手の軌跡
        st = self.state
        if st is not None and st.history and self.anim is None:
            player, _f, _t, path = st.history[-1]
            c = QColor(PIECE_COLORS[player])
            c.setAlphaF(0.55)
            pen = QPen(c, max(2.0, scale * 0.09), Qt.DashLine)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            for a, b in zip(path, path[1:]):
                painter.drawLine(self.node_xy(a, layout), self.node_xy(b, layout))

        # 空ノード
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(COLOR_NODE))
        nr = max(3.0, scale * 0.16)
        for i in range(GEO.n):
            painter.drawEllipse(self.node_xy(i, layout), nr, nr)

        # 移動可能先のハイライト
        if self.targets:
            painter.setPen(QPen(QColor("#ffffff"), 2))
            hl = QColor("#10b981")
            hl.setAlphaF(0.75)
            painter.setBrush(hl)
            for t in self.targets:
                painter.drawEllipse(self.node_xy(t, layout), scale * 0.27, scale * 0.27)

        # 駒
        if st is not None:
            pr = scale * 0.40
            for i in range(GEO.n):
                p = st.board[i]
                if p < 0:
                    continue
                if self.anim is not None and i == self.anim["from"]:
                    continue
                self._draw_piece(painter, self.node_xy(i, layout), pr, p, st.labels[i], i == self.selected)
            if self.anim is not None:
                pt = self._anim_point(layout)
                self._draw_piece(painter, pt, pr * 1.08, self.anim["player"], self.anim["label"], True)

        # 凡例
        self._draw_legend(painter)
        painter.end()

    def _anim_point(self, layout):
        path = self.anim["path"]
        segs = len(path) - 1
        t = min(self.anim["t"], 1.0) * segs
        k = min(int(t), segs - 1)
        u = t - k
        a = self.node_xy(path[k], layout)
        b = self.node_xy(path[k + 1], layout)
        x = a.x() + (b.x() - a.x()) * u
        y = a.y() + (b.y() - a.y()) * u
        # ジャンプは少し弧を描く
        if len(path) > 2 or not _is_step(path[0], path[1]):
            y -= math.sin(u * math.pi) * layout[0] * 0.35
        return QPointF(x, y)

    def _draw_piece(self, painter, center, radius, player, label, selected):
        base = QColor(PIECE_COLORS[player])
        # 影
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 70))
        painter.drawEllipse(QPointF(center.x() + radius * 0.10, center.y() + radius * 0.16), radius, radius)
        # 立体感のあるグラデーション
        grad = QRadialGradient(QPointF(center.x() - radius * 0.35, center.y() - radius * 0.4), radius * 1.4)
        grad.setColorAt(0.0, base.lighter(175))
        grad.setColorAt(0.45, base)
        grad.setColorAt(1.0, base.darker(170))
        painter.setBrush(QBrush(grad))
        pen = QPen(QColor("#fde047") if selected else QColor("#ffffff"), 3.0 if selected else 1.6)
        painter.setPen(pen)
        painter.drawEllipse(center, radius, radius)
        # 番号
        f = QFont(self.font())
        f.setBold(True)
        f.setPixelSize(max(8, int(radius * 0.95)))
        painter.setFont(f)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius), Qt.AlignCenter, str(label))

    def _draw_legend(self, painter):
        st = self.state
        if st is None:
            return
        f = QFont(self.font())
        f.setPointSizeF(9)
        painter.setFont(f)
        items = []
        for p in st.players:
            items.append(
                (
                    PIECE_COLORS[p.idx],
                    "P%d駒: 15個 (%s陣地10 + 基底辺5 → %s)"
                    % (p.idx + 1, CAMP_NAMES[p.start_key], CAMP_NAMES[p.goal_key]),
                )
            )
        fm = painter.fontMetrics()
        widths = [24 + fm.horizontalAdvance(text) for _, text in items]
        total = sum(widths) + 18 * (len(items) - 1)
        x = (self.width() - total) / 2.0
        y = self.height() - 22
        for (color, text), w in zip(items, widths):
            painter.setPen(QPen(QColor("#ffffff"), 1.5))
            painter.setBrush(QColor(color))
            painter.drawEllipse(QPointF(x + 8, y), 7, 7)
            painter.setPen(QColor("#1f2937"))
            painter.drawText(QPointF(x + 20, y + fm.ascent() / 2.0 - 1), text)
            x += w + 18


# =============================================================================
# 5. スタート画面（設定ダイアログ）
# =============================================================================


class StartDialog(QDialog):
    def __init__(self, parent=None, previous=None):
        super().__init__(parent)
        self.setWindowTitle("ダイヤモンドゲーム - ゲーム設定")
        self.setModal(True)
        self.setMinimumWidth(600)
        prev = previous or GameConfig()

        root = QVBoxLayout(self)
        title = QLabel("ダイヤモンドゲーム")
        tf = title.font()
        tf.setPointSize(18)
        tf.setBold(True)
        title.setFont(tf)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color:%s;" % COLOR_TEXT_DARK)
        root.addWidget(title)
        sub = QLabel("121ノード / 15駒ルール（Spec v1.2）")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color:%s;" % COLOR_TEXT_SUB)
        root.addWidget(sub)

        # 1. 人数
        g1 = QGroupBox("1. プレイヤー人数")
        h1 = QHBoxLayout(g1)
        self.rb2 = QRadioButton("2人対戦（緑 vs 黄）")
        self.rb3 = QRadioButton("3人対戦（緑 vs 水色 vs 橙）")
        grp = QButtonGroup(self)
        grp.addButton(self.rb2)
        grp.addButton(self.rb3)
        (self.rb3 if prev.num_players == 3 else self.rb2).setChecked(True)
        h1.addWidget(self.rb2)
        h1.addWidget(self.rb3)
        root.addWidget(g1)

        # 2. 割り当て
        g2 = QGroupBox("2. 各プレイヤーの割り当て")
        form = QFormLayout(g2)
        self.combos = []
        self.combo_labels = []
        for i in range(3):
            cb = QComboBox()
            cb.addItem("人間 (User)", "human")
            cb.addItem("AI", "ai")
            default = prev.kinds[i] if i < len(prev.kinds) else "ai"
            cb.setCurrentIndex(0 if default == "human" else 1)
            lab = QLabel()
            form.addRow(lab, cb)
            self.combos.append(cb)
            self.combo_labels.append(lab)
        root.addWidget(g2)

        # 3. 難易度
        g3 = QGroupBox("3. AIの難易度")
        v3 = QVBoxLayout(g3)
        self.rb_easy = QRadioButton("初級（Easy）: 貪欲法\n　ゴール方向へ最も進む手・最長ジャンプを優先")
        self.rb_hard = QRadioButton("上級（Hard）: ミニマックス + アルファベータ枝刈り\n　探索深さ最大3手・ゴール距離／ジャンプ数／到達駒数で評価")
        grp2 = QButtonGroup(self)
        grp2.addButton(self.rb_easy)
        grp2.addButton(self.rb_hard)
        (self.rb_easy if prev.difficulty == "easy" else self.rb_hard).setChecked(True)
        v3.addWidget(self.rb_easy)
        v3.addWidget(self.rb_hard)
        root.addWidget(g3)

        # 4. ルールオプション
        g4 = QGroupBox("4. ルール")
        v4 = QVBoxLayout(g4)
        self.chk_exit = QCheckBox("ゴール退出禁止（ゴールに入った駒は外へ出られない）")
        self.chk_exit.setChecked(prev.forbid_goal_exit)
        v4.addWidget(self.chk_exit)
        self.chk_spoil = QCheckBox("妨害防止ルール（ゴールが相手駒で塞がれていても、\n全15マスが埋まり自駒が1つ以上あれば勝利）")
        self.chk_spoil.setChecked(prev.anti_spoil)
        v4.addWidget(self.chk_spoil)
        note = QLabel("※ 人間の手番で5秒間操作しないと自動操作に切り替わります。\n"
                      "　 自動操作中に盤面をクリックすると、以後はずっと手動操作になります。")
        note.setStyleSheet("color:#555;")
        note.setWordWrap(True)
        v4.addWidget(note)
        root.addWidget(g4)

        buttons = QDialogButtonBox()
        start = buttons.addButton("ゲーム開始", QDialogButtonBox.AcceptRole)
        start.setDefault(True)
        buttons.addButton("終了", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.rb2.toggled.connect(self._refresh)
        self.rb3.toggled.connect(self._refresh)
        self._refresh()

    def _refresh(self):
        n = 3 if self.rb3.isChecked() else 2
        camps = PLAYER_CAMPS[n]
        for i in range(3):
            visible = i < n
            self.combos[i].setVisible(visible)
            self.combo_labels[i].setVisible(visible)
            if visible:
                self.combo_labels[i].setText(
                    "Player %d（%s駒 / %s陣地）:" % (i + 1, PIECE_COLOR_NAMES[i], CAMP_NAMES[camps[i]])
                )

    def config(self):
        n = 3 if self.rb3.isChecked() else 2
        kinds = [self.combos[i].currentData() for i in range(n)]
        return GameConfig(
            num_players=n,
            kinds=kinds,
            difficulty="easy" if self.rb_easy.isChecked() else "hard",
            forbid_goal_exit=self.chk_exit.isChecked(),
            anti_spoil=self.chk_spoil.isChecked(),
        )


# =============================================================================
# 6. メインウィンドウ（ゲーム進行・5秒タイマー・自動操作）
# =============================================================================


class MainWindow(QMainWindow):
    IDLE_SECONDS = 5.0
    MAX_PLIES_PER_PLAYER = 200  # 総手数の上限（超過で引き分け）
    STALL_PLIES_PER_PLAYER = 30  # ゴール到達数が伸びない手数の上限（膠着で引き分け）

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ダイヤモンドゲーム (Chinese Checkers)")
        self.config = None
        self.state = None
        self.token = 0  # AI結果の世代管理（新規ゲーム・キャンセル時に更新）
        self.workers = []
        self.ai_thinking = False
        self.animating = False
        self.paused = False
        self.auto_mode = []  # 人間プレイヤーごとの自動操作フラグ
        self.manual_locked = False  # True なら以後ずっと手動操作
        self.turn_touched = False  # この手番で盤面を操作したか
        self.deadline = 0.0

        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(8, 8, 8, 8)

        # ヘッダー
        header = QHBoxLayout()
        self.turn_label = QLabel()
        hf = self.turn_label.font()
        hf.setPointSize(12)
        hf.setBold(True)
        self.turn_label.setFont(hf)
        self.timer_label = QLabel()
        tf = self.timer_label.font()
        tf.setPointSize(12)
        tf.setBold(True)
        self.timer_label.setFont(tf)
        self.btn_pause = QPushButton("一時停止")
        self.btn_new = QPushButton("新しいゲーム（設定）")
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_new.clicked.connect(self.new_game_dialog)
        header.addWidget(self.turn_label)
        header.addStretch(1)
        header.addWidget(self.timer_label)
        header.addSpacing(16)
        header.addWidget(self.btn_pause)
        header.addWidget(self.btn_new)
        v.addLayout(header)

        self.board = BoardWidget()
        self.board.clicked_node.connect(self.on_board_click)
        v.addWidget(self.board, 1)

        self.status = self.statusBar()

        self.countdown = QTimer(self)
        self.countdown.setInterval(100)
        self.countdown.timeout.connect(self.on_countdown)

        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(16)
        self.anim_timer.timeout.connect(self.on_anim_tick)

        self.resize(760, 860)

    # ---------------- ゲーム開始 ----------------
    def new_game_dialog(self):
        dlg = StartDialog(self, self.config)
        if dlg.exec_() != QDialog.Accepted:
            if self.state is None:
                return False
            return True
        self.start_game(dlg.config())
        return True

    def start_game(self, config):
        self.cancel_pending()
        self.config = config
        self.state = GameState(config)
        self.auto_mode = [False] * config.num_players
        self.manual_locked = False
        self.paused = False
        self.btn_pause.setText("一時停止")
        self.board.state = self.state
        self.board.selected = None
        self.board.targets = {}
        self.board.anim = None
        self.engines = []
        for i in range(config.num_players):
            # 自動操作（人間の代打ち）は「最適手」を指すため常に上級AIを使う
            diff = config.difficulty if config.kinds[i] == "ai" else "hard"
            self.engines.append(AIEngine(self.state.players, config.forbid_goal_exit, diff, config.anti_spoil))
        kinds = " / ".join(
            "P%d:%s" % (i + 1, "人間" if k == "human" else "AI") for i, k in enumerate(config.kinds)
        )
        self.status.showMessage(
            "新しいゲーム: %d人対戦  %s  難易度:%s  ゴール退出禁止:%s  妨害防止:%s"
            % (
                config.num_players,
                kinds,
                "初級" if config.difficulty == "easy" else "上級",
                "ON" if config.forbid_goal_exit else "OFF",
                "ON" if config.anti_spoil else "OFF",
            )
        )
        self.board.update()
        QTimer.singleShot(300, self.begin_turn)

    def cancel_pending(self):
        self.token += 1
        self.ai_thinking = False
        self.countdown.stop()
        self.anim_timer.stop()
        self.animating = False
        if self.board.anim is not None:
            self.board.anim = None

    # ---------------- 手番管理 ----------------
    def is_human(self, p):
        return self.config.kinds[p] == "human"

    def legal_moves(self, p, with_paths=True):
        st = self.state
        return generate_moves(st.board, st.pos[p], st.players[p].goal, self.config.forbid_goal_exit, with_paths)

    def begin_turn(self):
        st = self.state
        if st is None or st.winner is not None or self.animating or self.ai_thinking:
            return
        p = st.current
        self.board.selected = None
        self.board.targets = {}
        self.turn_touched = False
        if st.ply >= self.MAX_PLIES_PER_PLAYER * len(st.players):
            self.finish_game(None, "%d手経過したため引き分けです。" % st.ply)
            return
        if st.ply - st.last_progress_ply >= self.STALL_PLIES_PER_PLAYER * len(st.players):
            self.finish_game(None, "ゴール到達数が%d手のあいだ伸びず、膠着状態のため引き分けです。" % (st.ply - st.last_progress_ply))
            return
        if not self.legal_moves(p, with_paths=False):
            self.status.showMessage("P%d は合法手がないためパスします。" % (p + 1))
            st.next_player()
            QTimer.singleShot(400, self.begin_turn)
            return
        self.update_header()
        if self.paused:
            return
        if not self.is_human(p):
            self.request_ai(p)
        elif self.auto_mode[p] and not self.manual_locked:
            self.request_ai(p)
        elif not self.manual_locked:
            self.deadline = time.time() + self.IDLE_SECONDS
            self.countdown.start()
            self.update_header()
        self.board.update()

    def update_header(self):
        st = self.state
        if st is None:
            return
        p = st.current
        who = "人間" if self.is_human(p) else "AI"
        self.turn_label.setText(
            "<span style='color:%s'>●</span> 手番: P%d（%s駒 / %s陣地 → %s）— %s　｜　%d手目"
            % (
                PIECE_COLORS[p],
                p + 1,
                PIECE_COLOR_NAMES[p],
                CAMP_NAMES[st.players[p].start_key],
                CAMP_NAMES[st.players[p].goal_key],
                who,
                st.ply + 1,
            )
        )
        if self.paused:
            self.timer_label.setText("<span style='color:#6b7280'>一時停止中</span>")
        elif self.is_human(p) and self.auto_mode[p] and not self.manual_locked:
            self.timer_label.setText("<span style='color:#b45309'>自動操作中（盤面クリックで手動に復帰）</span>")
        elif self.is_human(p) and self.countdown.isActive():
            remain = max(0, math.ceil(self.deadline - time.time()))
            self.timer_label.setText("<span style='color:#b91c1c'>残り %d秒...</span>" % remain)
        elif self.is_human(p) and self.manual_locked:
            self.timer_label.setText("<span style='color:#1d4ed8'>手動操作モード</span>")
        elif self.is_human(p):
            self.timer_label.setText("<span style='color:#1d4ed8'>操作中</span>")
        elif self.ai_thinking:
            self.timer_label.setText("<span style='color:#374151'>AI思考中...</span>")
        else:
            self.timer_label.setText("")

    def on_countdown(self):
        if self.state is None or self.paused:
            self.countdown.stop()
            return
        if time.time() >= self.deadline:
            self.countdown.stop()
            p = self.state.current
            if not self.manual_locked and not self.turn_touched:
                self.auto_mode[p] = True
                self.status.showMessage("5秒間操作がなかったため、P%d を自動操作モードに切り替えました。" % (p + 1))
                self.board.selected = None
                self.board.targets = {}
                self.request_ai(p)
        self.update_header()

    # ---------------- AI ----------------
    def request_ai(self, p):
        st = self.state
        self.ai_thinking = True
        self.update_header()
        token = self.token
        worker = AIWorker(token, self.engines[p], st.board, st.pos, p, st.last_move_of(p))
        worker.finished_move.connect(self.on_ai_move)
        worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
        self.workers.append(worker)
        worker.start()

    def _cleanup_worker(self, worker):
        if worker in self.workers:
            self.workers.remove(worker)
        worker.deleteLater()

    def on_ai_move(self, token, move):
        if token != self.token:
            return  # キャンセル済み（手動復帰・新規ゲーム）
        self.ai_thinking = False
        st = self.state
        p = st.current
        if self.paused:
            self.update_header()
            return
        if move is None:
            st.next_player()
            self.begin_turn()
            return
        if self.is_human(p) and (self.manual_locked or not self.auto_mode[p]):
            # 自動操作がちょうど解除された
            self.begin_turn()
            return
        f, t, path = move
        self.start_move(p, f, t, path)

    # ---------------- 人間の操作 ----------------
    def on_board_click(self, node):
        st = self.state
        if st is None or st.winner is not None:
            return
        # 自動操作中のクリック → 自動操作を即時解除し、以後ずっと手動操作
        if any(self.auto_mode) and not self.manual_locked:
            self.manual_locked = True
            self.auto_mode = [False] * len(self.auto_mode)
            self.status.showMessage("手動操作に復帰しました。以後は自動操作に切り替わりません。")
            if self.is_human(st.current) and self.ai_thinking:
                self.token += 1  # 代打ちAIの結果を破棄
                self.ai_thinking = False
        p = st.current
        if not self.is_human(p) or self.animating or self.ai_thinking or self.paused:
            self.update_header()
            return
        # 操作があったので、この手番の5秒タイマーは停止
        self.turn_touched = True
        self.countdown.stop()

        if node in self.board.targets:
            path = self.board.targets[node]
            f = self.board.selected
            self.board.selected = None
            self.board.targets = {}
            self.start_move(p, f, node, path)
            return
        if node >= 0 and st.board[node] == p:
            self.board.selected = node
            self.board.targets = {t: path for f, t, path in self.legal_moves(p) if f == node}
            if not self.board.targets:
                self.status.showMessage("その駒は動かせません。")
            else:
                self.status.showMessage("P%d の駒 %d を選択（移動先: %d箇所）" % (p + 1, st.labels[node], len(self.board.targets)))
        else:
            self.board.selected = None
            self.board.targets = {}
        self.update_header()
        self.board.update()

    # ---------------- 移動アニメーション ----------------
    def start_move(self, p, f, t, path):
        st = self.state
        self.animating = True
        self.board.anim = {"player": p, "label": st.labels[f], "path": list(path), "from": f, "t": 0.0}
        segs = len(path) - 1
        self.anim_duration = 0.16 * segs + 0.05
        self.anim_start = time.time()
        self.anim_timer.start()
        self.update_header()

    def on_anim_tick(self):
        a = self.board.anim
        if a is None:
            self.anim_timer.stop()
            return
        a["t"] = (time.time() - self.anim_start) / self.anim_duration
        if a["t"] >= 1.0:
            self.anim_timer.stop()
            self.board.anim = None
            self.animating = False
            self.finish_move(a["player"], a["path"][0], a["path"][-1], a["path"])
        self.board.update()

    def finish_move(self, p, f, t, path):
        st = self.state
        st.apply(p, f, t, path)
        if len(path) == 2 and _is_step(f, t):
            kind = "ステップ"
        else:
            kind = "ジャンプ×%d" % (len(path) - 1)
        self.status.showMessage("P%d: 駒%d %s %s → %s" % (p + 1, st.labels[t], kind, GEO.nodes[f], GEO.nodes[t]))
        if st.winner is not None:
            self.board.update()
            self.finish_game(st.winner)
            return
        st.next_player()
        self.begin_turn()

    def finish_game(self, winner, draw_msg=""):
        self.countdown.stop()
        st = self.state
        if winner is None:
            st.winner = -1
            msg = draw_msg
        else:
            msg = "P%d（%s駒）の勝利です！（%d手）" % (winner + 1, PIECE_COLOR_NAMES[winner], st.ply)
        self.turn_label.setText("ゲーム終了 — " + msg)
        self.timer_label.setText("")
        self.status.showMessage(msg)
        box = QMessageBox(self)
        box.setWindowTitle("ゲーム終了")
        box.setText(msg + "\n\nもう一度プレイしますか？")
        again = box.addButton("新しいゲーム", QMessageBox.AcceptRole)
        box.addButton("閉じる", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() == again:
            self.new_game_dialog()

    def toggle_pause(self):
        if self.state is None or self.state.winner is not None:
            return
        self.paused = not self.paused
        self.btn_pause.setText("再開" if self.paused else "一時停止")
        if self.paused:
            self.countdown.stop()
            self.update_header()
        else:
            if not self.ai_thinking and not self.animating:
                self.begin_turn()
            self.update_header()

    def closeEvent(self, ev):
        self.token += 1
        for w in list(self.workers):
            w.wait(3000)
        super().closeEvent(ev)


def _is_step(f, t):
    q1, r1 = GEO.nodes[f]
    q2, r2 = GEO.nodes[t]
    return (q2 - q1, r2 - r1) in DIRECTIONS


# =============================================================================
# 7. エントリーポイント
# =============================================================================


def main():
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    font = QFont("Meiryo UI")
    font.setPointSize(10)
    app.setFont(font)
    win = MainWindow()
    if not win.new_game_dialog():
        return 0
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
