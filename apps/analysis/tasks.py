import csv
import io
import logging

from celery import shared_task
from django.core.files.base import ContentFile

from apps.teams.utils import current_team

from .models import AnalysisStatus, TranscriptAnalysis

logger = logging.getLogger("ocs.analysis")


@shared_task
def process_transcript_analysis(analysis_id):
    """
    Process a transcript analysis job:
    1. Load the transcript analysis record
    2. Get all selected sessions
    3. For each session:
        a. Get all messages to form the transcript
        b. For each query in the analysis:
            i. Query OpenAI with the transcript and the query
            ii. Store the result
    4. Compile all results into a CSV file
    5. Update the analysis record with the result file
    """
    try:
        analysis = TranscriptAnalysis.objects.select_related("experiment", "created_by").get(id=analysis_id)

        # Update status to processing
        analysis.status = AnalysisStatus.PROCESSING
        analysis.save(update_fields=["status"])

        with current_team(analysis.team):
            # Get the appropriate LLM provider based on the model's type
            # Use the first available provider of the right type
            llm_service = analysis.llm_provider.get_llm_service()

            # Get model using the selected provider model
            model_name = analysis.llm_provider_model.name
            llm = llm_service.get_chat_model(model_name, temperature=0.1)  # Low temperature for analysis

            # Prepare results container
            results = []
            header_row = ["Session ID", "Participant"]

            # Get all the queries first
            queries = list(analysis.queries.all().order_by("order"))

            # Add query names to header
            for query in queries:
                header_row.append(query.name or query.prompt[:50])

            # Add header row to results
            results.append(header_row)

            # Process each session
            sessions = analysis.sessions.all().prefetch_related("chat__messages")

            for session in sessions:
                # Get the transcript
                transcript = ""
                for message in session.chat.messages.all().order_by("created_at"):
                    prefix = "User: " if message.message_type == "human" else "Bot: "
                    transcript += f"{prefix}{message.content}\n\n"

                # Skip empty transcripts
                if not transcript.strip():
                    continue

                # Start row with session info
                session_row = [session.external_id, str(session.participant) if session.participant else "Anonymous"]

                # Process each query
                for query in queries:
                    prompt = f"""
                    Analyze the following conversation transcript according to this query:
                    
                    QUERY: {query.prompt}
                    
                    TRANSCRIPT:
                    {transcript}
                    
                    Please provide a concise, objective response to the query based only on the transcript content.
                    """

                    if query.output_format:
                        prompt += f"\n\nFormat your response as: {query.output_format}"

                    try:
                        # Process with LLM
                        response = llm.invoke(prompt)
                        answer = response.content

                        # Add to row
                        session_row.append(answer)
                    except Exception as e:
                        logger.exception(f"Error processing query for session {session.id}: {e}")
                        session_row.append(f"ERROR: {str(e)}")

                # Add completed row to results
                results.append(session_row)

            # Create CSV file from results
            csv_buffer = io.StringIO()
            writer = csv.writer(csv_buffer)
            for row in results:
                writer.writerow(row)

            # Save the result file
            filename = f"{analysis.name}_results.csv"
            analysis.result_file.save(filename, ContentFile(csv_buffer.getvalue().encode("utf-8")), save=False)
            analysis.status = AnalysisStatus.COMPLETED
            analysis.save()

            return {"status": "success", "analysis_id": analysis_id}

    except Exception as e:
        logger.exception(f"Error processing transcript analysis {analysis_id}: {e}")
        try:
            analysis = TranscriptAnalysis.objects.get(id=analysis_id)
            analysis.status = AnalysisStatus.FAILED
            analysis.error_message = str(e)
            analysis.save()
        except:  # noqa E722
            pass
        return {"status": "error", "error": str(e)}
