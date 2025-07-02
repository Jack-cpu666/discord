import tkinter as tk
from tkinter import ttk
import random

class AdvancedQuizApp:
    """
    An advanced GUI quiz application with extremely difficult questions
    and a modern, polished user interface.
    """
    
    def __init__(self, root):
        self.root = root
        self.root.title("🧠 Advanced AI Challenge Quiz")
        self.root.geometry("700x550")
        self.root.configure(bg='#2c3e50')
        self.root.attributes('-topmost', True)
        
        # Configure modern styling
        self.setup_styles()
        
        # --- Database of extremely challenging questions ---
        self.questions = [
            {
                "question": "In quantum field theory, what is the primary mechanism by which the Higgs field gives mass to the W and Z bosons in the Standard Model?",
                "options": [
                    "Spontaneous symmetry breaking through the Higgs mechanism",
                    "Gauge invariance preservation via covariant derivatives", 
                    "Yukawa coupling interactions with fermion fields",
                    "Loop corrections from virtual particle exchanges"
                ],
                "answer": "Spontaneous symmetry breaking through the Higgs mechanism"
            },
            {
                "question": "Which philosophical paradox, proposed by Derek Parfit, challenges the notion of personal identity by suggesting that teleportation scenarios reveal the incoherence of survival?",
                "options": [
                    "The Ship of Theseus",
                    "The Teletransporter Paradox",
                    "Mary's Room Thought Experiment",
                    "The Chinese Room Argument"
                ],
                "answer": "The Teletransporter Paradox"
            },
            {
                "question": "In computational complexity theory, what is the defining characteristic that separates the complexity class BPP from P?",
                "options": [
                    "BPP allows probabilistic algorithms with bounded error probability",
                    "BPP requires exponential space complexity",
                    "BPP only contains decision problems with unique solutions",
                    "BPP is the class of problems solvable by quantum computers"
                ],
                "answer": "BPP allows probabilistic algorithms with bounded error probability"
            },
            {
                "question": "What is the primary enzymatic mechanism by which cytochrome c oxidase (Complex IV) couples electron transfer to proton pumping across the inner mitochondrial membrane?",
                "options": [
                    "Conformational changes driven by heme redox state transitions",
                    "Direct ATP synthesis through substrate-level phosphorylation",
                    "Quinone cycling between ubiquinol and ubiquinone forms",
                    "Iron-sulfur cluster electron tunneling mechanisms"
                ],
                "answer": "Conformational changes driven by heme redox state transitions"
            },
            {
                "question": "In the context of Byzantine fault tolerance, what is the minimum number of nodes required in an asynchronous distributed system to tolerate 'f' Byzantine failures while maintaining safety and liveness properties?",
                "options": [
                    "2f + 1 nodes",
                    "3f + 1 nodes", 
                    "4f + 1 nodes",
                    "f² + f + 1 nodes"
                ],
                "answer": "3f + 1 nodes"
            }
        ]
        
        self.current_question_data = None
        self.selected_option = tk.StringVar()
        self.question_number = 1
        
        self.create_ui()
        self.setup_new_question()
    
    def setup_styles(self):
        """Configure modern styling for the application."""
        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        # Configure custom styles
        self.style.configure('Title.TLabel', 
                           background='#2c3e50', 
                           foreground='#ecf0f1',
                           font=('Segoe UI', 16, 'bold'))
        
        self.style.configure('Question.TLabel',
                           background='#34495e',
                           foreground='#ecf0f1', 
                           font=('Segoe UI', 12),
                           relief='flat',
                           padding=20)
        
        self.style.configure('Option.TRadiobutton',
                           background='#34495e',
                           foreground='#bdc3c7',
                           font=('Segoe UI', 11),
                           focuscolor='#3498db')
        
        self.style.configure('Feedback.TLabel',
                           background='#2c3e50',
                           font=('Segoe UI', 11, 'italic'),
                           padding=10)
        
        self.style.configure('Modern.TButton',
                           font=('Segoe UI', 10, 'bold'),
                           padding=10)
    
    def create_ui(self):
        """Create the modern user interface."""
        # Main container with gradient-like effect
        self.main_frame = tk.Frame(self.root, bg='#2c3e50', padx=30, pady=20)
        self.main_frame.pack(fill="both", expand=True)
        
        # Title section
        title_frame = tk.Frame(self.main_frame, bg='#2c3e50')
        title_frame.pack(fill="x", pady=(0, 20))
        
        self.title_label = tk.Label(title_frame, 
                                   text="🎯 ADVANCED KNOWLEDGE CHALLENGE",
                                   font=('Segoe UI', 18, 'bold'),
                                   bg='#2c3e50',
                                   fg='#e74c3c')
        self.title_label.pack()
        
        self.subtitle_label = tk.Label(title_frame,
                                      text="Testing the limits of AI reasoning",
                                      font=('Segoe UI', 10, 'italic'),
                                      bg='#2c3e50',
                                      fg='#95a5a6')
        self.subtitle_label.pack(pady=(5, 0))
        
        # Question counter
        self.counter_label = tk.Label(self.main_frame,
                                     text="Question 1 of 5",
                                     font=('Segoe UI', 12, 'bold'),
                                     bg='#2c3e50',
                                     fg='#3498db')
        self.counter_label.pack(pady=(0, 15))
        
        # Question container with modern styling
        question_container = tk.Frame(self.main_frame, bg='#34495e', relief='flat', bd=2)
        question_container.pack(fill="x", pady=(0, 20))
        
        self.question_label = tk.Label(question_container,
                                      text="",
                                      font=('Segoe UI', 12, 'bold'),
                                      bg='#34495e',
                                      fg='#ecf0f1',
                                      wraplength=620,
                                      justify='left',
                                      padx=20,
                                      pady=20)
        self.question_label.pack(fill="x")
        
        # Options container
        self.options_frame = tk.Frame(self.main_frame, bg='#2c3e50')
        self.options_frame.pack(fill="x", pady=(0, 20))
        
        # Feedback section
        feedback_container = tk.Frame(self.main_frame, bg='#2c3e50')
        feedback_container.pack(fill="x", pady=(0, 15))
        
        self.feedback_label = tk.Label(feedback_container,
                                      text="🤖 Waiting for AI response...",
                                      font=('Segoe UI', 11, 'italic'),
                                      bg='#2c3e50',
                                      fg='#f39c12')
        self.feedback_label.pack()
        
        # Control buttons
        button_frame = tk.Frame(self.main_frame, bg='#2c3e50')
        button_frame.pack(fill="x")
        
        self.new_question_button = tk.Button(button_frame,
                                           text="🔄 Next Challenge",
                                           font=('Segoe UI', 11, 'bold'),
                                           bg='#3498db',
                                           fg='white',
                                           relief='flat',
                                           padx=20,
                                           pady=8,
                                           cursor='hand2',
                                           command=self.setup_new_question)
        self.new_question_button.pack(side='left')
        
        self.difficulty_label = tk.Label(button_frame,
                                        text="💀 EXTREME DIFFICULTY",
                                        font=('Segoe UI', 10, 'bold'),
                                        bg='#2c3e50',
                                        fg='#e74c3c')
        self.difficulty_label.pack(side='right')
    
    def setup_new_question(self):
        """Set up a new challenging question with improved UI."""
        # Clear previous options
        for widget in self.options_frame.winfo_children():
            widget.destroy()
        
        # Get random question
        self.current_question_data = random.choice(self.questions)
        question_text = self.current_question_data["question"]
        options = self.current_question_data["options"].copy()
        random.shuffle(options)
        
        # Update question display
        self.question_label.config(text=question_text)
        self.counter_label.config(text=f"Question {self.question_number} of 5")
        self.feedback_label.config(text="🤖 Analyzing question complexity...", fg='#f39c12')
        self.selected_option.set(None)
        
        # Create modern radio buttons
        for i, option in enumerate(options):
            option_frame = tk.Frame(self.options_frame, bg='#34495e', relief='flat', bd=1)
            option_frame.pack(fill="x", pady=3, padx=10)
            
            rb = tk.Radiobutton(option_frame,
                               text=f"{chr(65+i)}. {option}",
                               variable=self.selected_option,
                               value=option,
                               font=('Segoe UI', 10),
                               bg='#34495e',
                               fg='#bdc3c7',
                               selectcolor='#3498db',
                               activebackground='#3498db',
                               activeforeground='white',
                               cursor='hand2',
                               padx=15,
                               pady=8,
                               command=self.check_manual_answer)
            rb.pack(anchor="w", fill="x")
        
        # Increment question counter
        self.question_number = (self.question_number % 5) + 1
    
    def check_manual_answer(self):
        """Provide sophisticated feedback for manual answers."""
        chosen = self.selected_option.get()
        correct_answer = self.current_question_data["answer"]
        
        if chosen == correct_answer:
            self.feedback_label.config(
                text="✅ Exceptional! You've mastered this advanced concept.",
                fg='#27ae60'
            )
        else:
            self.feedback_label.config(
                text=f"❌ Close, but not quite. The correct answer requires deeper analysis.\n💡 Correct: {correct_answer}",
                fg='#e74c3c'
            )

if __name__ == "__main__":
    root = tk.Tk()
    app = AdvancedQuizApp(root)
    root.mainloop()