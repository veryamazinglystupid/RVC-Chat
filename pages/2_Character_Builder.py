import hashlib
import json
import os
import streamlit as st
from webui import MENU_ITEMS, TTS_MODELS, config, get_cwd, i18n, DEVICE_OPTIONS
from webui.chat import init_assistant_template, init_llm_options, init_model_config, init_model_params, Character, load_character_data
from webui.downloader import OUTPUT_DIR
st.set_page_config(layout="wide",menu_items=MENU_ITEMS)

from webui.audio import save_input_audio
from webui.components import file_uploader_form, initial_voice_conversion_params, voice_conversion_form

import sounddevice as sd
from lib.model_utils import get_hash

from webui.contexts import SessionStateContext

import time
from types import SimpleNamespace

from webui.utils import gc_collect, get_filenames, get_index, get_optimal_torch_device

CWD = get_cwd()

def get_model_list():
    models_list =  [os.path.relpath(path,CWD) for path in get_filenames(root=os.path.join(CWD,"models"),folder="LLM",exts=["bin","gguf"])]
    return models_list

def get_voice_list():
    models_list = [os.path.relpath(path,CWD) for path in get_filenames(root=os.path.join(CWD,"models"),folder="RVC",exts=["pth"])]
    return models_list

def get_character_list():
    models_list =  [os.path.relpath(path,CWD) for path in get_filenames(root=os.path.join(CWD,"models"),folder="Characters",exts=["json"])]
    return models_list

def init_state():
    state = SimpleNamespace(
        voice_models=get_voice_list(),
        voice_model=None,
        characters=get_character_list(),
        selected_character=None,
        model_config=init_model_config(),
        assistant_template=init_assistant_template(),
        model_params=init_model_params(),
        model_list=get_model_list(),
        tts_options=initial_voice_conversion_params(),
        llm_options=init_llm_options(),
        messages = [],
        user = "",
        device=get_optimal_torch_device(),
        LLM=None,
        tts_method=None,
        character=None
    )
    return vars(state)

def refresh_data(state):
    state.model_list = get_model_list()
    state.voice_models = get_voice_list()
    state.characters = get_character_list()
    return state

def save_character(state):
    character_file = os.path.join(CWD,"models","Characters",f"{state.assistant_template.name}.json")
    with open(character_file,"w") as f:
        loaded_state = {
            "assistant_template": vars(state.assistant_template),
            "tts_options": vars(state.tts_options),
            "voice": state.voice_model,
            "tts_method": state.tts_method
        }
        f.write(json.dumps(loaded_state,indent=2))
    state = refresh_data(state)
    if state.character: state.character.character_data = load_character_data(character_file)
    return state

def load_character(state):
    with open(state.selected_character,"r") as f:
        loaded_state = json.load(f)
        state.assistant_template = SimpleNamespace(**loaded_state["assistant_template"])
        
        state.tts_options = vars(state.tts_options)
        state.tts_options.update(loaded_state["tts_options"])
        state.tts_options = SimpleNamespace(**state.tts_options)
        state.voice_model = loaded_state["voice"]
        state.tts_method = loaded_state["tts_method"]
    state = refresh_data(state)
    return state

def save_model_config(state):
    fname = os.path.join(CWD,"models","LLM","config.json")
    key = get_hash(state.model_params.fname)

    if os.path.isfile(fname):
        with open(fname,"r") as f:
            data = json.load(f)
    else:
        data = {}

    with open(fname,"w") as f:
        data[key] = {
            "version": 2,
            "params": vars(state.model_params),
            "config": vars(state.model_config),
            "options": vars(state.llm_options),
        }
        f.write(json.dumps(data,indent=2))
    state = refresh_data(state)
    return state

def load_model_config(state):
    fname = os.path.join(CWD,"models","LLM","config.json")
    key = get_hash(state.selected_llm)

    with open(fname,"r") as f:
        data = json.load(f) if os.path.isfile(fname) else {}    
        model_data = data[key] if key in data else {**vars(init_model_config()), **vars(init_model_params()), **vars(init_llm_options())}

        if "version" in model_data and model_data["version"]==2:
            state.model_params = SimpleNamespace(**model_data["params"])
            state.model_config = SimpleNamespace(**model_data["config"])
            state.llm_options = SimpleNamespace(**model_data["options"])
        else: # old version
            state.model_config.prompt_template = model_data["prompt_template"]
            state.model_config.chat_template = model_data["chat_template"]
            state.model_config.instruction = model_data["instruction"]
            state.model_config.mapper = model_data["mapper"]
            state.model_params.n_ctx = model_data["n_ctx"]
            state.model_params.n_gpu_layers = model_data["n_gpu_layers"]
            state.llm_options.max_tokens = model_data["max_tokens"]
    state = refresh_data(state)
    return state

def render_model_config_form(state):
    state.model_config.instruction = st.text_area("Instruction",value=state.model_config.instruction)
    state.model_config.chat_template = st.text_area("Chat Template",value=state.model_config.chat_template)
    state.model_config.prompt_template = st.text_area("Prompt Template",value=state.model_config.prompt_template,height=400)
    state.model_config.mapper = st.data_editor(state.model_config.mapper,
                                                        column_order=("_index","value"),
                                                        use_container_width=False,
                                                        num_rows="fixed",
                                                        disabled=["_index"],
                                                        hide_index=False)
    return state

def render_model_params_form(state):
    state.model_params.n_ctx = st.slider("Max Context Length", min_value=512, max_value=4096, step=512, value=state.model_params.n_ctx)
    state.model_params.n_gpu_layers = st.slider("GPU Layers", min_value=0, max_value=64, step=4, value=state.model_params.n_gpu_layers)
    state.llm_options.max_tokens = st.slider("New Tokens",min_value=24,max_value=256,step=8,value=state.llm_options.max_tokens)
    return state

def render_llm_form(state):
    if not state.selected_llm: st.markdown("*Please save your model config below if it doesn't exist!*")
    elif st.button("Load LLM Config",disabled=not state.selected_llm): state=load_model_config(state)
    
    with st.form("model.loader"):
        state = render_model_params_form(state)
        state = render_model_config_form(state)

        if st.form_submit_button("Save Configs",disabled=not state.selected_llm):
            state = save_model_config(state)
            st.experimental_rerun()
    return state

def render_tts_options_form(state):

    col1, col2 =st.columns(2)
    state.tts_method = col1.selectbox(
                i18n("tts.model.selectbox"),
                options=TTS_MODELS,
                index=get_index(TTS_MODELS,state.tts_method),
                format_func=lambda option: option.upper()
                )
    state.voice_model = col2.selectbox(
            i18n("inference.voice.selectbox"),
            options=state.voice_models,
            index=get_index(state.voice_models,state.voice_model),
            format_func=lambda option: os.path.basename(option).split(".")[0]
            )
    state.tts_options = voice_conversion_form(state.tts_options)
    return state

def render_assistant_template_form(state):
    state.assistant_template.name = st.text_input("Character Name",value=state.assistant_template.name)
    ROLE_OPTIONS = ["CHARACTER", "USER"]
    state.assistant_template.background = st.text_area("Background", value=state.assistant_template.background, max_chars=1000)
    state.assistant_template.personality = st.text_area("Personality", value=state.assistant_template.personality, max_chars=1000)
    st.write("Example Dialogue")
    state.assistant_template.examples = st.data_editor(state.assistant_template.examples,
                                                        column_order=("role","content"),
                                                        column_config={
                                                            "role": st.column_config.SelectboxColumn("Role",options=ROLE_OPTIONS,required=True),
                                                            "content": st.column_config.TextColumn("Content",required=True)
                                                        },
                                                        use_container_width=True,
                                                        num_rows="dynamic",
                                                        hide_index =True)
    state.assistant_template.greeting = st.text_area("Greeting",value=state.assistant_template.greeting,max_chars=1000)
    return state

def render_character_form(state):
    if not state.selected_character: st.markdown("*Please create a character below if it doesn't exist!*")
    elif st.button("Load Character Info",disabled=not state.selected_character): state=load_character(state)
        
    with st.form("character"):
        state = render_tts_options_form(state)
        state = render_assistant_template_form(state)
        if st.form_submit_button("Save"):
            state = save_character(state)
            st.experimental_rerun()
    
    return state

if __name__=="__main__":
    with SessionStateContext("character_builder",init_state()) as state:
        
        st.title("Character Builder")
        
        if st.button("Refresh Files"):
            state = refresh_data(state)

        # chat settings
        state.selected_character = st.selectbox("Your Character",
                                            options=state.characters,
                                            index=get_index(state.characters,state.selected_character),
                                            format_func=lambda x: os.path.basename(x))
        state = render_character_form(state)