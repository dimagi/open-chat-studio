## Login Credentials
Username: tester@playwright.com
Password: My0riginalP@ssw0rd!

# Core Workflows

## Flow 1: Create and Test a Chatbot

1. Log in
2. Go to the **Chatbots** tab
3. Click **Add New** to add a chatbot
   - Name: `My First Chatbot`
   - Leave description empty
   - Create the chatbot
4. Verify that you see an **LLM node**, an **Input node**, and an **Output node**, all connected
5. In the breadcrumbs at the top, click on the chatbot name ("My First Chatbot")
6. Verify the chatbot is in **Draft** mode
7. Go to the **Versions** tab
   - Click **Create Version**
   - Description: `Added my first chatbot`
   - Check **Set as published version**
   - Create the version
   - Wait ~4 seconds
8. At the top right, there will be three buttons — click the **Speech** button
   - Click **Published Version**
   - Now you can chat with the chatbot
9. Exchange three messages:
   - Send: `Hi` — wait for response
   - Send: `Tell me a joke` — wait for response
   - Send: `That's great` — wait for response
10. In the left sidebar, click **Chatbots** tab
11. Find the created chatbot in the list and click it
12. Verify that a session appears in the **Sessions** table (just verify there is one)

---

## Flow 2: Evaluations, Datasets, and Annotations

### Create an Evaluator

1. In the sidebar, go to **Evaluations** → **Evaluators**
2. Click **Add New**
   - Name: `My First Evaluator`
   - Evaluator Type: **LLM Evaluator**
   - First dropdown (model provider): `Working OpenAI`
   - Second dropdown (model): `o4-mini`
   - Prompt: `Evaluate friendliness. Output "friendly" if the conversation was friendly, otherwise "unfriendly"`
   - Output Schema:
     - Field name: `friendliness`
     - Type: `text`
3. Create the evaluator

### Create a Dataset

1. In the left sidebar, go to **Datasets** → **Add New**
   - Name: `My Data Set`
   - Select **From Sessions** radio
   - Select **All Experiment Sessions**
   - Verify all selected
2. Create the dataset

### Create and Run an Evaluation

1. In the left sidebar, go to **Evaluations** → **Add New**
   - Name: `My First Eval`
   - Dataset: select the one just created
   - Evaluators: select the evaluator just created
   - Check **Run generation step before evaluation**
   - Chatbot: `My First Chatbot`
   - Chatbot Version: **Latest Published Version**
2. Create the evaluation
3. On the evaluations list, click the **Run** (play button) on the first row

### Create and Use an Annotation

1. In the left sidebar, go to **Annotations** → **Add New**
   - Name: `My First Annotation`
   - Number of reviews required: leave as-is
   - Schema Fields:
     - Name: `accuracy`
     - Type: `text`
     - Description: leave empty
2. Create the annotation
3. Click **Annotations** in the left sidebar to see the annotation queue
4. Click the first annotation just created
5. Click **Add Sessions**
   - Select the first session checkbox
   - Scroll down and click **Add to Queue**
   - This redirects back to the annotation
6. Verify the session has been added
7. Click **Start Annotating**
   - On the left: session messages
   - On the right: the `accuracy` label with a text field
   - Enter: `5`
   - Click **Submit**
8. Verify on the annotation dashboard:
   - Items table shows one item (the session)
   - Status: **Completed**
   - Annotations column shows: `test user; accuracy; 5`

### Verify Evaluation Results

1. Go to the **Evaluations** tab
2. Find the evaluation created earlier
3. In the **Actions** column, click the middle button (**Preview** / info icon)
