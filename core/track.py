from __future__ import division
from sys import version_info
import time
import sys
import traceback

from core.constants import CONFIG
from core.files import load_program, save_program
from core.functions import calculate_line, RunningPrograms, find_distance, get_items
from core.messages import *

if version_info == 2:
    range = xrange
    

def running_processes(q_recv, q_send, background_send):
    """Check for running processes.
    As refreshing the list takes some time but not CPU, this is put in its own thread
    and sends the currently running program to the backgrund process.
    """
    running = RunningPrograms()
    previous = None
        
    while True:
            
        received_data = q_recv.get()

        if 'Reload' in received_data:
            running.reload_file()
            NOTIFY(PROGRAM_RELOAD)

        if 'Update' in received_data:
            running.refresh()
            current = running.check()
            if current != previous:
                if current is None:
                    NOTIFY(PROGRAM_QUIT)
                else:
                    NOTIFY(PROGRAM_STARTED, current)
                NOTIFY.send(q_send)
                background_send.put({'Program': current})
                previous = current


def _save_wrapper(q_send, program_name, data, new_program=False):
    
    NOTIFY(SAVE_START)
    NOTIFY.send(q_send)
    saved = False

    #Get how many attempts to use
    if new_program:
        max_attempts = CONFIG['Save']['MaximumAttemptsSwitch']
    else:
        max_attempts = CONFIG['Save']['MaximumAttemptsNormal']

    #Attempt to save
    for i in range(max_attempts):
        if save_program(program_name, data):
            NOTIFY(SAVE_SUCCESS)
            NOTIFY.send(q_send)
            saved = True
            break
        
        else:
            if max_attempts == 1:
                NOTIFY(SAVE_FAIL)
                return
            NOTIFY(SAVE_FAIL_RETRY, CONFIG['Save']['WaitAfterFail'],
                         i, max_attempts)
            NOTIFY.send(q_send)
            time.sleep(CONFIG['Save']['WaitAfterFail'])
            
    if not saved:
        NOTIFY(SAVE_FAIL_END)

        
def background_process(q_recv, q_send):
    """Function to handle all the data from the main thread."""
    try:
        NOTIFY(START_THREAD)
        NOTIFY.send(q_send)
        
        store = {'Data': load_program(),
                 'LastProgram': None,
                 'Resolution': None}
        
        NOTIFY(DATA_LOADED)
        NOTIFY(QUEUE_SIZE, q_recv.qsize())
        NOTIFY.send(q_send)

        maps = store['Data']['Maps']
        tick_count = store['Data']['Ticks']['Current']
        
        while True:
            
            received_data = q_recv.get()
            check_resolution = False
            
            if 'Save' in received_data:
                _save_wrapper(q_send, store['LastProgram'], store['Data'], False)
                NOTIFY(QUEUE_SIZE, q_recv.qsize())

            if 'Program' in received_data:
                current_program = received_data['Program']
                
                if current_program != store['LastProgram']:

                    check_resolution = True
                    if current_program is None:
                        NOTIFY(PROGRAM_LOADING)
                    else:
                        NOTIFY(PROGRAM_LOADING, current_program)
                    NOTIFY.send(q_send)
                        
                    _save_wrapper(q_send, store['LastProgram'], store['Data'], True)
                        
                    store['LastProgram'] = current_program
                    store['Data'] = load_program(current_program)
                    maps = store['Data']['Maps']
                    tick_count = store['Data']['Ticks']['Current']
                        
                    if store['Data']['Ticks']['Total']:
                        NOTIFY(DATA_LOADED)
                    else:
                        NOTIFY(DATA_NOTFOUND)
                            
                    NOTIFY(QUEUE_SIZE, q_recv.qsize())
                NOTIFY.send(q_send)

            if 'Resolution' in received_data:
                check_resolution = True
                store['Resolution'] = received_data['Resolution']

            #Make sure resolution exists as a key
            if check_resolution:
                if store['Resolution'] not in maps['Tracks']:
                    maps['Tracks'][store['Resolution']] = {}
                if store['Resolution'] not in maps['Clicks']:
                    maps['Clicks'][store['Resolution']] = [{}, {}, {}]
                if store['Resolution'] not in maps['Speed']:
                    maps['Speed'][store['Resolution']] = {}
                if store['Resolution'] not in maps['Combined']:
                    maps['Combined'][store['Resolution']] = {}
            
            #Record key presses
            if 'KeyPress' in received_data:
                for key in received_data['KeyPress']:
                    try:
                        store['Data']['Keys']['Pressed'][key] += 1
                    except KeyError:
                        store['Data']['Keys']['Pressed'][key] = 1
            
            #Record time keys are held down
            if 'KeyHeld' in received_data:
                for key in received_data['KeyHeld']:
                    try:
                        store['Data']['Keys']['Held'][key] += 1
                    except KeyError:
                        store['Data']['Keys']['Held'][key] = 1

            #Record mouse clicks
            if 'MouseClick' in received_data:
                for mouse_button, mouse_click in received_data['MouseClick']:
                    try:
                        maps['Clicks'][store['Resolution']][mouse_button][mouse_click] += 1
                    except KeyError:
                        maps['Clicks'][store['Resolution']][mouse_button][mouse_click] = 1
            
            #Calculate and track mouse movement
            if 'MouseMove' in received_data:
                start, end = received_data['MouseMove']
                distance = find_distance(end, start)
                combined = distance * tick_count['Speed']
                
                #Calculate the pixels in the line
                if start is None:
                    mouse_coordinates = [end]
                else:
                    mouse_coordinates = [start, end] + calculate_line(start, end)

                #Write each pixel to the dictionary
                for pixel in mouse_coordinates:
                    maps['Tracks'][store['Resolution']][pixel] = tick_count['Tracks']
                    try:
                        if maps['Speed'][store['Resolution']][pixel] < distance:
                            raise KeyError()
                    except KeyError:
                        maps['Speed'][store['Resolution']][pixel] = distance
                    try:
                        if maps['Combined'][store['Resolution']][pixel] < combined:
                            raise KeyError()
                    except KeyError:
                        maps['Combined'][store['Resolution']][pixel] = combined
                        
                tick_count['Tracks'] += 1
                tick_count['Speed'] += 1
                
                #Compress tracks if the count gets too high
                if tick_count['Tracks'] > CONFIG['CompressMaps']['TrackMaximum']:
                    compress_multplier = CONFIG['CompressMaps']['TrackReduction']
                    NOTIFY(MOUSE_COMPRESS_START, 'track')
                    NOTIFY.send(q_send)
                    
                    tracks = maps['Tracks']
                    for resolution in tracks.keys():
                        tracks[resolution] = {k: int(v // compress_multplier)
                                              for k, v in get_items(tracks[resolution])}
                        tracks[resolution] = {k: v for k, v in get_items(tracks[resolution]) if v}
                        if not tracks[resolution]:
                            del tracks[resolution]
                    NOTIFY(MOUSE_COMPRESS_END, 'track')
                    NOTIFY(QUEUE_SIZE, q_recv.qsize())
                    tick_count['Tracks'] //= compress_multplier

                #Compress speed map if the count gets too high
                if tick_count['Speed'] > CONFIG['CompressMaps']['SpeedMaximum']:
                    compress_multplier = CONFIG['CompressMaps']['SpeedReduction']
                    NOTIFY(MOUSE_COMPRESS_START, 'speed')
                    NOTIFY.send(q_send)
                            
                    speed = maps['Speed']
                    for resolution in speed.keys():
                        speed[resolution] = {k: int(v // compress_multplier)
                                             for k, v in get_items(speed[resolution])}
                        speed[resolution] = {k: v for k, v in get_items(speed[resolution]) if v}
                        if not speed[resolution]:
                            del speed[resolution]
                            
                    combined = maps['Combined']
                    for resolution in combined.keys():
                        combined[resolution] = {k: int(v // compress_multplier)
                                                for k, v in get_items(combined[resolution])}
                        combined[resolution] = {k: v for k, v in get_items(combined[resolution]) if v}
                        if not combined[resolution]:
                            del combined[resolution]
                    NOTIFY(MOUSE_COMPRESS_END, 'speed')
                    NOTIFY(QUEUE_SIZE, q_recv.qsize())
                    tick_count['Speed'] //= compress_multplier
                
            #Increment the amount of time the script has been running for
            if 'Ticks' in received_data:
                store['Data']['Ticks']['Total'] += received_data['Ticks']
            store['Data']['Ticks']['Recorded'] += 1

            NOTIFY.send(q_send)
            
            
    except Exception as e:
        q_send.put(traceback.format_exc())
        return
