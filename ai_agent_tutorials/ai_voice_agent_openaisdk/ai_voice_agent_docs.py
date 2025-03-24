from typing import List, Dict, Optional
from dataclasses import dataclass
from pathlib import Path
import os
from firecrawl import FirecrawlApp
from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.http.models import Distance, VectorParams
from fastembed import TextEmbedding
from agents import Agent, ModelSettings, function_tool, Runner
from openai import OpenAI, AsyncOpenAI
from openai.helpers import LocalAudioPlayer
import textwrap
import tempfile
import uuid
import numpy as np
from typing import Callable
from urllib.parse import urlparse
from dotenv import load_dotenv
import asyncio
import json
from datetime import datetime
import time
import streamlit as st

load_dotenv()

def init_session_state():
    """Initialize session state variables for storing API keys and configurations."""
    defaults = {
        "initialized": False,
        "qdrant_url": "",
        "qdrant_api_key": "",
        "firecrawl_api_key": "",
        "openai_api_key": "",
        "doc_url": "",
        "setup_complete": False,
        "client": None,
        "embedding_model": None,
        "processor_agent": None,
        "tts_agent": None,
        "selected_voice": "coral"  # Default voice
    }
    
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

def sidebar_config():
    """Render and handle the configuration sidebar."""
    with st.sidebar:
        st.title("🔑 Configuration")
        st.markdown("---")
        
        # API Keys and URLs
        st.session_state.qdrant_url = st.text_input(
            "Qdrant URL",
            value=st.session_state.qdrant_url,
            type="password"
        )
        st.session_state.qdrant_api_key = st.text_input(
            "Qdrant API Key",
            value=st.session_state.qdrant_api_key,
            type="password"
        )
        st.session_state.firecrawl_api_key = st.text_input(
            "Firecrawl API Key",
            value=st.session_state.firecrawl_api_key,
            type="password"
        )
        st.session_state.openai_api_key = st.text_input(
            "OpenAI API Key",
            value=st.session_state.openai_api_key,
            type="password"
        )
        
        st.markdown("---")
        st.session_state.doc_url = st.text_input(
            "Documentation URL",
            value=st.session_state.doc_url,
            placeholder="https://docs.example.com"
        )
        
        # Voice selection
        st.markdown("---")
        st.markdown("### 🎤 Voice Settings")
        voices = ["alloy", "ash", "ballad", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer", "verse"]
        st.session_state.selected_voice = st.selectbox(
            "Select Voice",
            options=voices,
            index=voices.index(st.session_state.selected_voice),
            help="Choose the voice for the audio response"
        )
        
        # Setup button
        if st.button("Initialize System", type="primary"):
            if all([
                st.session_state.qdrant_url,
                st.session_state.qdrant_api_key,
                st.session_state.firecrawl_api_key,
                st.session_state.openai_api_key,
                st.session_state.doc_url
            ]):
                progress_placeholder = st.empty()
                with progress_placeholder.container():
                    try:
                        # Setup Qdrant
                        st.markdown("🔄 Setting up Qdrant connection...")
                        client, embedding_model = setup_qdrant_collection(
                            st.session_state.qdrant_url,
                            st.session_state.qdrant_api_key
                        )
                        st.session_state.client = client
                        st.session_state.embedding_model = embedding_model
                        st.markdown("✅ Qdrant setup complete!")
                        
                        # Crawl documentation
                        st.markdown("🔄 Crawling documentation pages...")
                        pages = crawl_documentation(
                            st.session_state.firecrawl_api_key,
                            st.session_state.doc_url
                        )
                        st.markdown(f"✅ Crawled {len(pages)} documentation pages!")
                        
                        # Store embeddings
                        store_embeddings(
                            client,
                            embedding_model,
                            pages,
                            "docs_embeddings"
                        )
                        
                        # Setup agents
                        processor_agent, tts_agent = setup_agents(
                            st.session_state.openai_api_key
                        )
                        st.session_state.processor_agent = processor_agent
                        st.session_state.tts_agent = tts_agent
                        
                        st.session_state.setup_complete = True
                        st.success("✅ System initialized successfully!")
                        
                    except Exception as e:
                        st.error(f"Error during setup: {str(e)}")
            else:
                st.error("Please fill in all the required fields!")

def setup_qdrant_collection(qdrant_url: str, qdrant_api_key: str, collection_name: str = "docs_embeddings"):
    print("\n--- Step 1: Setting up Qdrant Collection ---")
    try:
        client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        print("✓ Connected to Qdrant")
        
        embedding_model = TextEmbedding()
        test_embedding = list(embedding_model.embed(["test"]))[0]
        embedding_dim = len(test_embedding)
        print(f"✓ Embedding model ready (dimension: {embedding_dim})")
        
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=embedding_dim, distance=Distance.COSINE)
        )
        print(f"✓ Created collection: {collection_name}")
        
        return client, embedding_model
    
    except Exception as e:
        if "already exists" in str(e):
            print(f"✓ Collection {collection_name} already exists")
            return client, embedding_model
        raise e

def crawl_documentation(firecrawl_api_key: str, url: str, output_dir: Optional[str] = None):
    print("\n--- Step 2: Crawling Documentation ---")
    try:
        firecrawl = FirecrawlApp(api_key=firecrawl_api_key)
        print(f"✓ Initialized Firecrawl")
        
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            print(f"✓ Created output directory: {output_dir}")
        
        print(f"Starting crawl of {url}...")
        
        pages = []
        
        response = firecrawl.crawl_url(
            url,
            params={
                'limit': 5,
                'scrapeOptions': {
                    'formats': ['markdown', 'html']
                }
            }
        )
        
        while True:
            if response.get('status') == 'scraping':
                print(f"Progress: {response.get('completed', 0)}/{response.get('total', 0)} pages")
                print(f"Credits used: {response.get('creditsUsed', 0)}")
            
            for page in response.get('data', []):
                content = page.get('markdown') or page.get('html', '')
                metadata = page.get('metadata', {})
                source_url = metadata.get('sourceURL', '')
                
                if output_dir and content:
                    filename = f"{uuid.uuid4()}.md"
                    filepath = os.path.join(output_dir, filename)
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(content)
                
                pages.append({
                    "content": content,
                    "url": source_url,
                    "metadata": {
                        "title": metadata.get('title', ''),
                        "description": metadata.get('description', ''),
                        "language": metadata.get('language', 'en'),
                        "crawl_date": datetime.now().isoformat()
                    }
                })
                
                print(f"✓ Processed page: {metadata.get('title', 'Untitled')}")
            
            next_url = response.get('next')
            if not next_url:
                break
                
            response = firecrawl.get(next_url)
            time.sleep(1)
        
        print(f"✓ Crawled {len(pages)} pages")
        return pages
    
    except Exception as e:
        print(f"Error crawling documentation: {str(e)}")
        raise e

def store_embeddings(client: QdrantClient, embedding_model: TextEmbedding, pages: List[Dict], collection_name: str):
    print("\n--- Step 3: Generating and Storing Embeddings ---")
    try:
        for page in pages:
            embedding = list(embedding_model.embed([page["content"]]))[0]
            
            client.upsert(
                collection_name=collection_name,
                points=[
                    models.PointStruct(
                        id=str(uuid.uuid4()),
                        vector=embedding.tolist(),
                        payload={
                            "content": page["content"],
                            "url": page["url"],
                            **page["metadata"]
                        }
                    )
                ]
            )
            print(f"✓ Stored embedding for: {page['metadata']['title'] or page['url']}")
        
        print(f"✓ Stored {len(pages)} embeddings in Qdrant")
    
    except Exception as e:
        print(f"Error storing embeddings: {str(e)}")
        raise e

def setup_agents(openai_api_key: str):
    print("\n--- Step 4: Setting up OpenAI Agents ---")
    try:
        # Set OpenAI API key in environment
        os.environ["OPENAI_API_KEY"] = openai_api_key
        print("✓ Set OpenAI API key in environment")
        
        processor_agent = Agent(
            name="Documentation Processor",
            instructions="""You are a helpful documentation assistant. Your task is to:
            1. Analyze the provided documentation content
            2. Answer the user's question clearly and concisely
            3. Include relevant examples when available
            4. Cite the source URLs when referencing specific content
            5. Keep responses natural and conversational
            6. Format your response in a way that's easy to speak out loud""",
            model="gpt-4o"
        )
        print("✓ Set up Documentation Processor Agent")

        tts_agent = Agent(
            name="Text-to-Speech Agent",
            instructions="""You are a text-to-speech agent. Your task is to:
            1. Convert the processed documentation response into natural speech
            2. Maintain proper pacing and emphasis
            3. Handle technical terms clearly
            4. Keep the tone professional but friendly
            5. Use appropriate pauses for better comprehension
            6. Ensure the speech is clear and well-articulated""",
            model="gpt-4o-mini-tts"
        )
        print("✓ Set up TTS Agent")
        
        return processor_agent, tts_agent
    
    except Exception as e:
        print(f"Error setting up agents: {str(e)}")
        raise e

async def process_query(
    query: str,
    client: QdrantClient,
    embedding_model: TextEmbedding,
    processor_agent: Agent,
    tts_agent: Agent,
    collection_name: str,
    openai_api_key: str
):
    try:
        # Generate query embedding
        query_embedding = list(embedding_model.embed([query]))[0]
        
        # Search in Qdrant
        search_response = client.query_points(
            collection_name=collection_name,
            query=query_embedding.tolist(),
            limit=3,
            with_payload=True
        )
        
        search_results = search_response.points if hasattr(search_response, 'points') else []
        
        if not search_results:
            raise Exception("No relevant documents found in the vector database")
        
        # Build context from search results
        context = "Based on the following documentation:\n\n"
        for result in search_results:
            payload = result.payload
            if not payload:
                continue
            url = payload.get('url', 'Unknown URL')
            content = payload.get('content', '')
            context += f"From {url}:\n{content}\n\n"
        
        context += f"\nUser Question: {query}\n\n"
        context += "Please provide a clear, concise answer that can be easily spoken out loud."
        
        # Process response with agents
        processor_result = await Runner.run(processor_agent, context)
        processor_response = processor_result.final_output
        
        tts_result = await Runner.run(tts_agent, processor_response)
        tts_response = tts_result.final_output
        
        # Generate audio
        async_openai = AsyncOpenAI(api_key=openai_api_key)
        audio_response = await async_openai.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice=st.session_state.selected_voice,
            input=processor_response,
            instructions=tts_response,
            response_format="mp3"
        )
        
        # Save audio to a temporary file
        temp_dir = tempfile.gettempdir()
        audio_path = os.path.join(temp_dir, f"response_{uuid.uuid4()}.mp3")
        
        # Write the audio content to the file
        with open(audio_path, "wb") as f:
            f.write(audio_response.content)
                
        return {
            "status": "success",
            "text_response": processor_response,
            "tts_instructions": tts_response,
            "audio_path": audio_path,
            "sources": [r.payload.get("url", "Unknown URL") for r in search_results if r.payload],
            "query_details": {
                "vector_size": len(query_embedding),
                "results_found": len(search_results),
                "collection_name": collection_name
            }
        }
    
    except Exception as e:
        print(f"\nError processing query: {str(e)}")
        return {
            "status": "error",
            "error": str(e),
            "query": query
        }

def run_streamlit():
    """Main Streamlit application."""
    st.set_page_config(
        page_title="AI Voice Documentation Agent Team",
        page_icon="🎙️",
        layout="wide"
    )
    
    init_session_state()
    sidebar_config()
    
    # Main content area
    st.title("🎙️ AI Voice Documentation Agent Team")
    st.markdown("""
    Get OpenAI SDK voice-powered answers to your documentation questions! Simply:
    1. Configure your API keys in the sidebar
    2. Enter the documentation URL you want to learn about or have questions about
    3. Ask your question below and get both text and voice responses
    """)
    
    # Query input and processing
    query = st.text_input(
        "What would you like to know about the documentation?",
        placeholder="e.g., How do I authenticate API requests?",
        disabled=not st.session_state.setup_complete
    )
    
    if query and st.session_state.setup_complete:
        with st.status("Processing your query...", expanded=True) as status:
            try:
                st.markdown("🔄 Searching documentation and generating response...")
                result = asyncio.run(process_query(
                    query,
                    st.session_state.client,
                    st.session_state.embedding_model,
                    st.session_state.processor_agent,
                    st.session_state.tts_agent,
                    "docs_embeddings",
                    st.session_state.openai_api_key
                ))
                
                if result["status"] == "success":
                    status.update(label="✅ Query processed!", state="complete")
                    
                    st.markdown("### Response:")
                    st.write(result["text_response"])
                    
                    if "audio_path" in result:
                        st.markdown(f"### 🔊 Audio Response (Voice: {st.session_state.selected_voice})")
                        # Pass the file path directly to st.audio
                        st.audio(result["audio_path"], format="audio/mp3", start_time=0)
                        
                        # For download button, we still need to read the bytes
                        with open(result["audio_path"], "rb") as audio_file:
                            audio_bytes = audio_file.read()
                            st.download_button(
                                label="📥 Download Audio Response",
                                data=audio_bytes,
                                file_name=f"voice_response_{st.session_state.selected_voice}.mp3",
                                mime="audio/mp3"
                            )
                    
                    st.markdown("### Sources:")
                    for source in result["sources"]:
                        st.markdown(f"- {source}")
                else:
                    status.update(label="❌ Error processing query", state="error")
                    st.error(f"Error: {result.get('error', 'Unknown error occurred')}")
                    
            except Exception as e:
                status.update(label="❌ Error processing query", state="error")
                st.error(f"Error processing query: {str(e)}")
    
    elif not st.session_state.setup_complete:
        st.info("👈 Please configure the system using the sidebar first!")

if __name__ == "__main__":
    run_streamlit()