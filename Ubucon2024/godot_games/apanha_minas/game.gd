extends Control

#colors
var col_dark_blue := Color("46425e")
var col_mid_blue := Color("15788c")
var col_light_blue := Color("00b9be")
var col_beige := Color("ffeecc")
var col_pink := Color("ffb0a3")
var col_dark_pink := Color("ff6973")

@onready var btn_exit = $BtnExit
@onready var btn_new_game : Button = get_node("BtnNewGame")
@onready var title = $Title
@onready var game_over_label = $GameOverLabel
@onready var back_ground = $BackGround

@onready var cell_holder = $CellHolder
@onready var is_game_over : bool = false
const CELL = preload("res://cell.tscn")

var cell_size := Vector2(74, 74)
var grid_size := Vector2(10, 6)
var grid_locs := []

var mine_amount : int
var mined_percent : float = 0.2
var mine_locs := []
var safe_locs := []
var cell_dict := {}

var score_text : String = "Blocos restantes: "

var dirs := [
	Vector2(-1, -1),
	Vector2(0, -1),
	Vector2(1, -1),
	Vector2(1, 0),
	Vector2(1, 1),
	Vector2(0, 1),
	Vector2(-1, 1),
	Vector2(-1, 0)
]

func _ready():
	randomize()
	btn_new_game.pressed.connect(_on_btn_new_game_pressed)
	mine_amount = int((grid_size.x * grid_size.y) * mined_percent)
	new_game()

func refresh_score() -> void:
	title.text = score_text + str(safe_locs.size())

func new_game() -> void:
	#cleanup
	if cell_holder.get_children().size() > 0:
		for i in cell_holder.get_children():
			i.queue_free()
			cell_holder.remove_child(i)
	is_game_over = false
	game_over_label.visible = false
	mine_locs.clear()
	cell_dict.clear()
	grid_locs.clear()
	safe_locs.clear()
	tweenback(col_dark_blue)
	#make new grid
	for x in grid_size.x:
		for y in grid_size.y:
			grid_locs.append(Vector2(x, y))
	#select mine placements
	var loc_pool : Array = grid_locs.duplicate()
	for i in mine_amount:
		var new_mine_loc = loc_pool.pick_random()
		mine_locs.append(new_mine_loc)
		loc_pool.erase(new_mine_loc)
	safe_locs = loc_pool
	refresh_score()
	#instantiate cells
	var disp := Vector2((grid_size.x / 2) * cell_size.x, (grid_size.y / 2) * cell_size.y)
	for loc in grid_locs:
		var new_cell = CELL.instantiate()
		cell_holder.add_child(new_cell)
		new_cell.game = self
		new_cell.self_grid_pos = loc
		new_cell.position = Vector2(loc.x * cell_size.x, loc.y * cell_size.y,) - disp
		var mined := false
		if mine_locs.has(loc):
			mined = true
		var mined_neighbors : int = check_neighbors(loc)
		cell_dict[str(loc)] = [new_cell, false, mined, mined_neighbors]
	#cell_dict[Vector2] = [noderef, bool is_it_digged, bool is_it_mined, int how_many_mined_neighbors]

func cell_got_clicked(pos : Vector2, cell : TextureButton) -> void:
	if cell_dict[str(pos)][2] == true:
		cell.reveal_cell(-1)
		if is_game_over == false:
			game_over(false)
	else:
		var mined_neighbors = cell_dict[str(pos)][3]
		cell.reveal_cell(mined_neighbors)
		cell_dict[str(pos)][1] = true
		safe_locs.erase(pos)
		refresh_score()
		if mined_neighbors == 0:
			a_blank_was_clicked(pos)
		if is_game_over == false:
			pass
		if safe_locs.is_empty():
			game_over(true)

func a_blank_was_clicked(pos : Vector2) -> void:
	var blank_neighbors = check_blank_neighbors(pos)
	if blank_neighbors.size() > 0:
		for neighbor in blank_neighbors:
			if safe_locs.has(neighbor.self_grid_pos):
				neighbor.on_left_click()
	for neighbor in all_neighbors(pos):
		neighbor.on_left_click()

func check_neighbors(loc : Vector2) -> int:
	var suspects : int = 0
	for dir in dirs:
		if mine_locs.has(loc + dir):
			suspects += 1
	return suspects

func check_blank_neighbors(loc : Vector2) -> Array:
	var blank_neighbors_arr := []
	for dir in dirs:
		var neighbor_loc : Vector2 = loc + dir
		if grid_locs.has(neighbor_loc):
			if cell_dict[str(neighbor_loc)][3] == 0:
				var noderef = cell_dict[str(neighbor_loc)][0]
				blank_neighbors_arr.append(noderef)
	return blank_neighbors_arr
	
func all_neighbors(loc : Vector2) -> Array:
	var neighbors_arr := []
	for dir in dirs:
		var neighbor_loc : Vector2 = loc + dir
		if grid_locs.has(neighbor_loc) && cell_dict[str(neighbor_loc)][1] == false:
			var noderef = cell_dict[str(neighbor_loc)][0]
			neighbors_arr.append(noderef)
	return neighbors_arr

func game_over(result : bool) -> void:
	is_game_over = true
	game_over_label.visible = true
	if result == false:
		#lost
		for cell in cell_holder.get_children():
			if cell.disabled == false:
				cell.on_left_click()
		game_over_label.text = "Perdeste! Tenta de novo!"
		tweenback(col_pink)
	else:
		#win
		game_over_label.text = "WOW! Ganhaste! és o/a MAIOR!"
		tweenback(col_mid_blue)

func tweenback(value : Color) -> void:
	var new_tween = create_tween()
	new_tween.tween_property(back_ground, "color", value, 0.5)
	new_tween.set_trans(Tween.TRANS_QUART)
	new_tween.set_ease(Tween.EASE_IN_OUT)

func _on_btn_new_game_pressed():
	new_game()

func _on_btn_exit_pressed():
	get_tree().quit()
