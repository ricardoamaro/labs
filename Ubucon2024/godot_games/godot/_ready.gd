extends Node


# Called when the node enters the scene tree for the first time.
func _ready() -> void:
	new_game()


func new_game() -> void:
	if cell_holder.get_children().size() > 0:
		for i in cell_holder.get_childre():
			i.queue_free()
			




# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta: float) -> void:
	pass
