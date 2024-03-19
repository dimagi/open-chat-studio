import axios, {AxiosInstance} from "axios";
import {BASE_URL_API} from "../constants/constants";
import {PipelineType} from "../types/pipeline";


const api: AxiosInstance = axios.create({
  baseURL: "",
});

/**
 * Updates an existing pipeline in the database.
 *
 * @param team
 * @param pipelineId
 * @param {PipelineType} updatedPipeline - The updated data.
 * @returns {Promise<any>} The updated pipeline data.
 * @throws Will throw an error if the update fails.
 */
export async function updatePipelineInDatabase(
  team: string,
  pipelineId: number,
  updatedPipeline: PipelineType
): Promise<PipelineType> {
  try {
    const response = await api.post(`${BASE_URL_API}a/${team}/pipelines/${pipelineId}/`, updatedPipeline);

    if (response?.status !== 200) {
      throw new Error(`HTTP error! status: ${response?.status}`);
    }
    return response.data;
  } catch (error) {
    console.error(error);
    throw error;
  }
}

/**
 * Fetches a pipeline from the database by ID.
 *
 * @param team
 * @param {number} pipelineId - The ID of the pipeline to fetch.
 * @returns {Promise<any>} The pipeline data.
 * @throws Will throw an error if fetching fails.
 */
export async function getPipelineFromDatabase(team: string, pipelineId: number): Promise<PipelineType> {
  try {
    const response = await api.get(`${BASE_URL_API}a/${team}/pipelines/${pipelineId}`);
    if (response.status !== 200) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    return response.data;
  } catch (error) {
    console.error(error);
    throw error;
  }
}
