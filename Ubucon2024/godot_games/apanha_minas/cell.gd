extends TextureButton

var game
var self_grid_pos : Vector2
var is_flagged := false

const SKULL = preload("res://assets/skull.png")
const DICE_EMPTY = preload("res://assets/dice_empty.png")
const DICE_1 = preload("res://assets/dice_1.png")
const DICE_2 = preload("res://assets/dice_2.png")
const DICE_3 = preload("res://assets/dice_3.png")
const DICE_4 = preload("res://assets/dice_4.png")
const DICE_5 = preload("res://assets/dice_5.png")
const DICE_6 = preload("res://assets/dice_6.png")
const DICE_7 = preload("res://assets/dice_7.png")
const DICE_8 = preload("res://assets/dice_8.png")
const FLAG_TRIANGLE = preload("res://assets/flag_triangle.png")
const D_6_OUTLINE = preload("res://assets/d6_outline.png")

signal left_click
signal right_click

func _ready():
	gui_input.connect(_on_Button_gui_input)
	left_click.connect(on_left_click)
	right_click.connect(on_right_click)
	texture_normal = D_6_OUTLINE

func reveal_cell(value : int) -> void:
	if value == -1:
		texture_disabled = SKULL
		modulate = game.col_dark_pink
	else:
		match value:
			0 : texture_disabled = DICE_EMPTY
			1 : texture_disabled = DICE_1
			2 : texture_disabled = DICE_2
			3 : texture_disabled = DICE_3
			4 : texture_disabled = DICE_4
			5 : texture_disabled = DICE_5
			6 : texture_disabled = DICE_6
			7 : texture_disabled = DICE_7
			8 : texture_disabled = DICE_8
	disabled = true

func flag() -> void:
	if is_flagged == false:
		is_flagged = true
		texture_normal = FLAG_TRIANGLE
	else:
		is_flagged = false
		texture_normal = D_6_OUTLINE

func _on_Button_gui_input(event):
	if event is InputEventMouseButton and event.pressed:
		match event.button_index:
			MOUSE_BUTTON_LEFT:
				left_click.emit()
			MOUSE_BUTTON_RIGHT:
				right_click.emit()

func on_left_click():
	game.cell_got_clicked(self_grid_pos, self)

func on_right_click():
	flag()
