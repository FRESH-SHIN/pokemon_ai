import asyncio
from pyboy import PyBoy
from consts import MAP_ID_TO_NAME
from gb_hooker import GBHooker
from memory_reader import MemoryReader
from llm_client import send_to_llm, capture_screen  # LLM과 이미지 캡처 함수 가져오기
from PIL import Image
memory_reader: MemoryReader
def extract_commands(command_response: str):
    # 행 단위로 나누고, /로 시작하는 행만 필터링
    commands = [line.strip() for line in command_response.split('\n') if line.strip().startswith('/')]
    return commands

async def llm_worker(game_state_queue, command_queue, is_working, pyboy, dialogues_queue):
    """
    게임 상태를 큐에서 받아 LLM에 요청을 보내고, 응답된 명령을 처리합니다.
    슬래시 명령 (/take_note, /joypad)을 지원하도록 확장되었습니다.
    """
    step_count = 0
    notes = []
    dialogues = ""
    region_notes = {}
    for i in MAP_ID_TO_NAME:
        region_notes[MAP_ID_TO_NAME[i]] = []
    while True:
        game_state, screen_ascii_data = await game_state_queue.get()
        if not dialogues_queue.empty():
            dialogues += await dialogues_queue.get() + "\n"
        image_data = capture_screen(pyboy)
        is_working.set()
        command_response = await send_to_llm(screen_ascii_data, game_state, image_data, notes, step_count, region_notes, dialogues)
        is_working.clear()
        if not command_response:
            print("[ERROR] No response from LLM.")
            continue

        command_texts = extract_commands(command_response)
        for command_text in command_texts:

            # 슬래시 명령 처리
            if command_text.startswith("/take_note"):
                note = command_text[len("/take_note"):].strip()
                notes.append(f"Step {step_count}: {note}")
                print(f"[NOTE ADDED] {step_count}: {note}")
            elif command_text.startswith("/take_map_note"):
                current_map =  game_state["overworld_state"]["current_map"]
                note = command_text[len("/take_map_note"):].strip()
                if region_notes in note:
                    region_notes[current_map].append(f"Step {step_count}: {note}")
                    print(f"[NOTE ADDED] {step_count}: {note}")
            elif command_text.startswith("/joypad"):
                buttons = command_text[len("/joypad"):].strip()
                button_list = [btn.strip() for btn in buttons.strip("[]").split(",") if btn.strip()]

                for btn in button_list:
                    btn = btn.lower()
                    if btn not in ["a", "b", "up", "down", "left", "right", "start"]:
                        print(f"[ERROR] Invalid button: {btn}")
                        continue
                    await command_queue.put(btn)
                print(f"[INFO] Joypad commands queued: {button_list}")
            
            else:
                print(f"[ERROR] Unknown command format: {command_response}")

        step_count += 1
async def game_loop(pyboy, memory_reader, game_state_queue, command_queue, is_working):
    """
    게임 실행 루프: LLM이 응답할 때까지는 계속 게임을 진행하면서 입력을 대기.
    """
    tick = 0
    while pyboy.tick():
        # LLM이 실행 중이지 않을 때만 새로운 게임 상태를 전송
        if command_queue.empty() and game_state_queue.empty() and not is_working.is_set():
            #print(f"LLM Working: {is_working.is_set()}")  # LLM이 실행 중인지 확인
            tick += 1
            if tick > 60 * 5:  # 5초마다 LLM에 새로운 상태 전송
                tick = 0
                game_state = memory_reader.get_game_state()
                game_screen_ascii = memory_reader.generate_overworld_markdown_from_memory()
                await game_state_queue.put((game_state, game_screen_ascii))

        # LLM이 보낸 명령을 적용
        if not command_queue.empty():
            button = await command_queue.get()
            print(f"Pressing button: {button}")
            pyboy.button(button, 10)

        await asyncio.sleep(1/60)  # 게임 루프가 너무 빠르게 실행되지 않도록 조절

async def main():
    rom_path = "data/pokered.gb"
    pyboy = PyBoy(rom_path, window="SDL2")
    pyboy.set_emulation_speed(0)  # 실시간 실행

    
    memory_reader = MemoryReader(pyboy)
    hooker = GBHooker(pyboy, memory_reader.symbol_map)
    
    # LLM과 PyBoy 간 데이터 교환을 위한 큐 생성
    dialogues_queue = asyncio.Queue()
    game_state_queue = asyncio.Queue()  # LLM에 보낼 게임 상태 저장
    command_queue = asyncio.Queue()  # LLM의 응답을 저장
    hooker.initHooks(dialogues_queue)
    # LLM 작업 상태를 관리하는 Event 객체 생성
    is_working = asyncio.Event()

    # LLM 작업을 백그라운드에서 실행 (종료될 필요 없음)
    asyncio.create_task(llm_worker(game_state_queue, command_queue, is_working, pyboy, dialogues_queue))

    # 게임 루프 실행
    await game_loop(pyboy, memory_reader, game_state_queue, command_queue, is_working)

    pyboy.stop()

if __name__ == "__main__":
    asyncio.run(main())
