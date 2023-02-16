import argparse
import json
import logging
import math
import os
import time

from tqdm import tqdm

from pad.api import pad_api
from pad.common.shared_types import Server
from pad.db.db_util import DbWrapper
from pad.raw import bonus
from pad.storage.wave import WaveItem


def parse_args():
    parser = argparse.ArgumentParser(description="Extracts PAD dungeon data.", add_help=False)

    input_group = parser.add_argument_group("Input")
    input_group.add_argument("--base_dir", required=True, help="Base directory for jsons")
    input_group.add_argument("--server", required=True, help="One of [NA, JP]")
    input_group.add_argument("--user_uuid", required=True, help="Account UUID")
    input_group.add_argument("--user_intid", required=True, help="Account code")

    input_group.add_argument("--dungeon_id", required=True, help="Dungeon ID")
    input_group.add_argument("--floor_id", required=True, help="Floor ID")
    input_group.add_argument("--loop_count", type=int, default=100, help="Number of entry attempts")
    input_group.add_argument("--stam_adjust", default=False,
                             action="store_true", help="Try to enter with 0 stamina.")

    output_group = parser.add_argument_group("Output")
    output_group.add_argument("--db_config", required=True, help="JSON database info")
    output_group.add_argument("--logsql", default=False,
                              action="store_true", help="Logs sql commands")
    output_group.add_argument("--stream_safe", action="store_true", help="Don't use fancy progress bars")

    help_group = parser.add_argument_group("Help")
    help_group.add_argument("-h", "--help", action="help",
                            help="Displays this help message and exits.")

    return parser.parse_args()


def pull_data(args, api_client=None, db_wrapper=None):
    if args.logsql:
        logging.getLogger('database').setLevel(logging.DEBUG)

    if args.server.upper() == 'NA':
        endpoint = pad_api.ServerEndpoint.NA
        server = Server.na
    elif args.server.upper() == 'JP':
        endpoint = pad_api.ServerEndpoint.JA
        server = Server.jp
    else:
        raise Exception('unexpected server:' + args.server)

    if api_client is None:
        api_client = pad_api.PadApiClient(endpoint, args.user_uuid, args.user_intid)
        api_client.login()
        print('load_player_data')
        api_client.load_player_data()

    friend_card = api_client.get_any_card_except_in_cur_deck()
    dungeon_id = args.dungeon_id
    floor_id = args.floor_id
    loop_count = args.loop_count
    pull_id = int(time.time())

    if db_wrapper is None:
        print('Connecting to database')
        with open(args.db_config) as f:
            db_config = json.load(f)

        db_wrapper = DbWrapper(False)
        db_wrapper.connect(db_config)

    stamina = db_wrapper.get_single_value(f"SELECT stamina FROM sub_dungeons"
                                          f" WHERE sub_dungeon_id = {int(dungeon_id) * 1000 + int(floor_id)};")
    bonuses = bonus.load_bonus_data(data_dir=os.path.join(args.base_dir, args.server.lower()), server=server)
    now = time.time()
    stam_bonus = next((b for b in bonuses
                       if b.start_timestamp <= now <= b.end_timestamp
                       and b.dungeon_id == dungeon_id
                       and (b.sub_dungeon_id is None or b.sub_dungeon_id % 1000 == floor_id)
                       and b.bonus_id == 5), None)
    if stam_bonus is not None:
        if stam_bonus.bonus_value:
            stamina = math.ceil(stamina * stam_bonus.bonus_value / 10000)
        elif stam_bonus.bonus_value_2:
            stamina = stam_bonus.bonus_value_2
        else:
            stamina = 0
    entry_id = int(db_wrapper.get_single_value("SELECT MAX(entry_id) FROM wave_data;"))
    if args.stam_adjust:
        stamina = 0
    print(f'entering {args.server.upper()} dungeon {dungeon_id} floor {floor_id} {loop_count} times using {stamina} stamina.')
    if args.stream_safe:
        iterator = range(loop_count)
    else:
        iterator = tqdm(range(loop_count), unit='runs')
    for _ in iterator:
        entry_id += 1
        entry_json = api_client.enter_dungeon(dungeon_id, floor_id, self_card=friend_card, stamina=stamina)
        wave_response = pad_api.extract_wave_response_from_entry(entry_json)
        leaders = entry_json['entry_leads']

        for stage_idx, floor in enumerate(wave_response.floors):
            for monster_idx, monster in enumerate(floor.monsters):
                wave_item = WaveItem(pull_id=pull_id, entry_id=entry_id, server=args.server.upper(),
                                     dungeon_id=dungeon_id, floor_id=floor_id, stage=stage_idx, slot=monster_idx,
                                     monster=monster, leader_id=leaders[0], friend_id=leaders[1])
                db_wrapper.insert_item(wave_item.insert_sql())

        if server != 'NA':
            time.sleep(.5)


if __name__ == '__main__':
    input_args = parse_args()
    pull_data(input_args)
